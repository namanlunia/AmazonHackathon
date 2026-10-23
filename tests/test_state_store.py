import asyncio

import pytest

from app.models import ContextEvent, UnifiedState
from app.state_store import InMemoryStateStore


@pytest.mark.asyncio
async def test_state_store_initial_and_update() -> None:
    store = InMemoryStateStore()
    user_id = "user-123"

    initial = await store.get(user_id)
    assert initial.user_id == user_id
    assert initial.version == 1
    assert initial.devices == {}
    assert initial.active_context is None

    def mutate(st: UnifiedState) -> None:
        st.history.append(
            ContextEvent(source_device_id="dev-1", description="event 1")
        )

    updated = await store.update(user_id, mutate)
    assert updated.version == 2
    assert len(updated.history) == 1
    assert updated.history[0].description == "event 1"


@pytest.mark.asyncio
async def test_state_store_concurrency() -> None:
    store = InMemoryStateStore()
    user_id = "concurrent-user"
    num_updates = 30

    async def worker(idx: int) -> None:
        def mut(st: UnifiedState) -> None:
            st.history.append(
                ContextEvent(source_device_id=f"dev-{idx}", description=f"action {idx}")
            )

        await store.update(user_id, mut)

    await asyncio.gather(*(worker(i) for i in range(num_updates)))

    final = await store.get(user_id)
    # Started at version 1, 30 increments = 31
    assert final.version == num_updates + 1
    assert len(final.history) == 20  # History cap of 20 newest


@pytest.mark.asyncio
async def test_state_store_isolation_and_reset() -> None:
    store = InMemoryStateStore()
    u1, u2 = "user-a", "user-b"

    def mut_u1(st: UnifiedState) -> None:
        st.history.append(ContextEvent(source_device_id="d1", description="u1 event"))

    await store.update(u1, mut_u1)

    s1 = await store.get(u1)
    s2 = await store.get(u2)
    assert s1.version == 2
    assert len(s1.history) == 1
    assert s2.version == 1
    assert len(s2.history) == 0

    reset_state = await store.reset(u1)
    assert reset_state.version == 1
    assert len(reset_state.history) == 0
