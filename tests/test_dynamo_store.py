import pytest

try:
    from moto import mock_aws
except ImportError:
    pytest.skip("moto is not installed", allow_module_level=True)

from botocore.exceptions import ClientError

from app.config import Settings
from app.models import ActiveContext, UnifiedState
from app.routers import Metrics
from app.state_store import DynamoStateStore


@pytest.mark.asyncio
async def test_dynamo_store_lifecycle_and_conflict() -> None:
    with mock_aws():
        settings = Settings(
            USE_AWS=True,
            AWS_REGION="us-east-1",
            DYNAMODB_TABLE="AmazonContinuityTestState",
        )
        store = DynamoStateStore(settings)

        # 1. ensure_table idempotence
        await store.ensure_table()
        await store.ensure_table()  # should not raise ResourceInUseException

        user_id = "test-dynamo-user"

        # 2. Initial write with attribute_not_exists
        def init_mutator(st: UnifiedState) -> None:
            st.active_context = ActiveContext(title="Initial Video", source_device_id="dev-1")

        state1 = await store.update(user_id, init_mutator)
        assert state1.version == 2
        assert state1.active_context is not None
        assert state1.active_context.title == "Initial Video"

        # 3. Conflict path: update the item underneath mid-update
        attempts_counter = 0

        def conflict_mutator(st: UnifiedState) -> None:
            nonlocal attempts_counter
            attempts_counter += 1
            if attempts_counter == 1:
                # Interfere by putting a newer version directly into DynamoDB
                store.client.put_item(
                    TableName=settings.DYNAMODB_TABLE,
                    Item={
                        "user_id": {"S": user_id},
                        "version": {"N": "99"},
                        "state": {"S": UnifiedState(user_id=user_id, version=99).model_dump_json()},
                        "updated_at": {"S": "2026-10-23T00:00:00"},
                        "ttl": {"N": "9999999999"},
                    },
                )
            st.active_context = ActiveContext(title="Second Video", source_device_id="dev-2")

        state2 = await store.update(user_id, conflict_mutator)
        # Attempt 1 failed due to version mismatch (:expected=2 vs actual=99);
        # Attempt 2 re-read version 99 and bumped it to 100!
        assert state2.version == 100
        assert attempts_counter >= 2

        # 4. Unconditional fallback after exhausting retries
        orig_put = store._sync_put_record
        calls_count = 0

        def failing_conditional_put(*args, **kwargs) -> None:
            nonlocal calls_count
            calls_count += 1
            # If a conditional expression is present, fail with ConditionalCheckFailedException
            if args[3] is not None:  # cond parameter
                error_response = {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "The conditional request failed",
                    }
                }
                raise ClientError(error_response, "PutItem")
            return orig_put(*args, **kwargs)

        store._sync_put_record = failing_conditional_put  # type: ignore[method-assign]
        degraded_before = Metrics.hub_store_writes_degraded_total

        def degraded_mutator(st: UnifiedState) -> None:
            st.active_context = ActiveContext(title="Degraded Video", source_device_id="dev-3")

        state_degraded = await store.update(user_id, degraded_mutator)
        assert state_degraded.active_context is not None
        assert state_degraded.active_context.title == "Degraded Video"
        assert Metrics.hub_store_writes_degraded_total > degraded_before

        store._sync_put_record = orig_put  # restore

        # 5. Reset
        reset_state = await store.reset(user_id)
        assert reset_state.version == 1
        assert reset_state.active_context is None

        fetched = await store.get(user_id)
        assert fetched.version == 1
        assert fetched.active_context is None
