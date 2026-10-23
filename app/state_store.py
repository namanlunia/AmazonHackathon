import asyncio
import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

from app.config import Settings, get_settings
from app.models import UnifiedState

logger = logging.getLogger(__name__)


class StateStore(ABC):
    """Abstract interface for user continuity state storage."""

    @abstractmethod
    async def get(self, user_id: str) -> UnifiedState:
        raise NotImplementedError

    @abstractmethod
    async def update(
        self, user_id: str, mutator: Callable[[UnifiedState], None]
    ) -> UnifiedState:
        raise NotImplementedError

    @abstractmethod
    async def reset(self, user_id: str) -> UnifiedState:
        raise NotImplementedError

    @abstractmethod
    async def ensure_table(self) -> None:
        raise NotImplementedError


class InMemoryStateStore(StateStore):
    """Thread-safe and async-safe in-memory state store."""

    def __init__(self) -> None:
        self._states: dict[str, UnifiedState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = threading.RLock()

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        with self._global_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            return self._locks[user_id]

    async def get(self, user_id: str) -> UnifiedState:
        lock = self._get_user_lock(user_id)
        async with lock:
            with self._global_lock:
                state = self._states.get(user_id)
                if state is None:
                    state = UnifiedState(user_id=user_id)
                    self._states[user_id] = state
                return state.model_copy(deep=True)

    async def update(
        self, user_id: str, mutator: Callable[[UnifiedState], None]
    ) -> UnifiedState:
        lock = self._get_user_lock(user_id)
        async with lock:
            with self._global_lock:
                current = self._states.get(user_id)
                if current is None:
                    current = UnifiedState(user_id=user_id)
                working_copy = current.model_copy(deep=True)

            mutator(working_copy)
            working_copy.version += 1
            working_copy.updated_at = datetime.now(UTC).isoformat()
            if len(working_copy.history) > 20:
                working_copy.history = working_copy.history[-20:]

            with self._global_lock:
                self._states[user_id] = working_copy.model_copy(deep=True)
                return working_copy.model_copy(deep=True)

    async def reset(self, user_id: str) -> UnifiedState:
        lock = self._get_user_lock(user_id)
        async with lock:
            with self._global_lock:
                fresh = UnifiedState(user_id=user_id)
                self._states[user_id] = fresh
                return fresh.model_copy(deep=True)

    async def ensure_table(self) -> None:
        pass


class DynamoStateStore(StateStore):
    """DynamoDB-backed state store with optimistic concurrency and TTL."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.table_name = self.settings.DYNAMODB_TABLE
        self.region = self.settings.AWS_REGION
        self.client = boto3.client("dynamodb", region_name=self.region)
        self._fallback_store = InMemoryStateStore()

    async def ensure_table(self) -> None:
        def _sync_ensure() -> None:
            try:
                self.client.describe_table(TableName=self.table_name)
                logger.info(
                    "DynamoDB table exists",
                    extra={"table_name": self.table_name, "event_type": "dynamo_check"},
                )
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                    logger.info(
                        "Creating DynamoDB table",
                        extra={"table_name": self.table_name, "event_type": "dynamo_create"},
                    )
                    self.client.create_table(
                        TableName=self.table_name,
                        KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
                        AttributeDefinitions=[
                            {"AttributeName": "user_id", "AttributeType": "S"}
                        ],
                        BillingMode="PAY_PER_REQUEST",
                    )
                    waiter = self.client.get_waiter("table_exists")
                    waiter.wait(TableName=self.table_name)
                    try:
                        self.client.update_time_to_live(
                            TableName=self.table_name,
                            TimeToLiveSpecification={
                                "Enabled": True,
                                "AttributeName": "ttl",
                            },
                        )
                    except ClientError as ttl_err:
                        logger.warning(f"Could not enable TTL: {ttl_err}")
                else:
                    raise

        await asyncio.to_thread(_sync_ensure)

    async def get(self, user_id: str) -> UnifiedState:
        def _sync_get() -> dict | None:
            response = self.client.get_item(
                TableName=self.table_name,
                Key={"user_id": {"S": user_id}},
                ConsistentRead=True,
            )
            return response.get("Item")

        try:
            item = await asyncio.to_thread(_sync_get)
            if not item or "state" not in item:
                return UnifiedState(user_id=user_id)
            data = json.loads(item["state"]["S"])
            return UnifiedState.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"DynamoDB get failed, falling back to in-memory store: {exc}",
                extra={"user_id": user_id, "event_type": "dynamo_get_fallback"},
            )
            return await self._fallback_store.get(user_id)

    def _sync_put_record(
        self,
        user_id: str,
        working_copy: UnifiedState,
        expected_version: int,
        cond: str,
        state_json: str,
        ttl_epoch: int,
    ) -> None:
        self.client.put_item(
            TableName=self.table_name,
            Item={
                "user_id": {"S": user_id},
                "version": {"N": str(working_copy.version)},
                "state": {"S": state_json},
                "updated_at": {"S": working_copy.updated_at},
                "ttl": {"N": str(ttl_epoch)},
            },
            ConditionExpression=cond,
            ExpressionAttributeValues={":expected": {"N": str(expected_version)}},
        )

    async def update(
        self, user_id: str, mutator: Callable[[UnifiedState], None]
    ) -> UnifiedState:
        for attempt in range(3):
            try:
                current_state = await self.get(user_id)
                expected_version = current_state.version
                working_copy = current_state.model_copy(deep=True)
                mutator(working_copy)
                working_copy.version += 1
                working_copy.updated_at = datetime.now(UTC).isoformat()
                if len(working_copy.history) > 20:
                    working_copy.history = working_copy.history[-20:]

                state_json = working_copy.model_dump_json()
                ttl_epoch = int(time.time() + (self.settings.STATE_TTL_DAYS * 86400))

                if expected_version == 1 and not current_state.devices and not current_state.active_context:
                    cond = "attribute_not_exists(user_id) OR version = :expected"
                else:
                    cond = "version = :expected"

                await asyncio.to_thread(
                    self._sync_put_record,
                    user_id,
                    working_copy,
                    expected_version,
                    cond,
                    state_json,
                    ttl_epoch,
                )
                return working_copy
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code == "ConditionalCheckFailedException":
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (2**attempt))
                        continue
                    logger.warning(
                        "DynamoDB conditional check failed after 3 attempts; falling back to in-memory",
                        extra={"user_id": user_id, "event_type": "dynamo_conflict"},
                    )
                    return await self._fallback_store.update(user_id, mutator)
                logger.warning(
                    f"DynamoDB put failed with client error ({code}), falling back: {exc}",
                    extra={"user_id": user_id, "event_type": "dynamo_error"},
                )
                return await self._fallback_store.update(user_id, mutator)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"DynamoDB update error, falling back: {exc}",
                    extra={"user_id": user_id, "event_type": "dynamo_error"},
                )
                return await self._fallback_store.update(user_id, mutator)

        return await self._fallback_store.update(user_id, mutator)

    async def reset(self, user_id: str) -> UnifiedState:
        fresh = UnifiedState(user_id=user_id)
        state_json = fresh.model_dump_json()
        ttl_epoch = int(time.time() + (self.settings.STATE_TTL_DAYS * 86400))

        def _sync_reset() -> None:
            self.client.put_item(
                TableName=self.table_name,
                Item={
                    "user_id": {"S": user_id},
                    "version": {"N": "1"},
                    "state": {"S": state_json},
                    "updated_at": {"S": fresh.updated_at},
                    "ttl": {"N": str(ttl_epoch)},
                },
            )

        try:
            await asyncio.to_thread(_sync_reset)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"DynamoDB reset failed, updating fallback store: {exc}")
        await self._fallback_store.reset(user_id)
        return fresh


_GLOBAL_STORE: StateStore | None = None


def get_store(settings: Settings | None = None) -> StateStore:
    """Return configured StateStore singleton."""
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        cfg = settings or get_settings()
        if cfg.USE_AWS:
            _GLOBAL_STORE = DynamoStateStore(cfg)
        else:
            _GLOBAL_STORE = InMemoryStateStore()
    return _GLOBAL_STORE
