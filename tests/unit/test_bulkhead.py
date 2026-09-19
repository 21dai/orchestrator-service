import asyncio

import pytest

from app.clients.bulkhead import Bulkhead, BulkheadFullError


async def hold_slot(bulkhead: Bulkhead, release: asyncio.Event, entered: asyncio.Event) -> None:
    """Toma un slot y lo retiene hasta que `release` se dispare."""
    async with bulkhead.slot():
        entered.set()
        await release.wait()


async def occupy(bulkhead: Bulkhead, n: int) -> tuple[asyncio.Event, list[asyncio.Task[None]]]:
    """Deja `n` tareas ocupando slots; devuelve el evento que las libera."""
    release = asyncio.Event()
    tasks = []
    for _ in range(n):
        entered = asyncio.Event()
        tasks.append(asyncio.create_task(hold_slot(bulkhead, release, entered)))
        await entered.wait()
    return release, tasks


async def test_deja_pasar_hasta_max_concurrent_a_la_vez() -> None:
    bulkhead = Bulkhead(max_concurrent=2, max_waiting=0, acquire_timeout_seconds=1)

    release, tasks = await occupy(bulkhead, 2)

    assert bulkhead.active == 2
    release.set()
    await asyncio.gather(*tasks)
    assert bulkhead.active == 0


async def test_rechaza_de_inmediato_cuando_slots_y_cola_estan_llenos() -> None:
    bulkhead = Bulkhead(max_concurrent=1, max_waiting=0, acquire_timeout_seconds=5)
    release, tasks = await occupy(bulkhead, 1)

    with pytest.raises(BulkheadFullError, match="en curso"):
        async with bulkhead.slot():
            pass

    release.set()
    await asyncio.gather(*tasks)


async def test_una_request_en_espera_toma_el_slot_cuando_se_libera() -> None:
    bulkhead = Bulkhead(max_concurrent=1, max_waiting=1, acquire_timeout_seconds=5)
    release, tasks = await occupy(bulkhead, 1)

    entered = asyncio.Event()

    async def waiter() -> None:
        async with bulkhead.slot():
            entered.set()

    waiting_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert bulkhead.waiting == 1
    assert not entered.is_set()

    release.set()
    await asyncio.gather(*tasks, waiting_task)
    assert entered.is_set()
    assert bulkhead.waiting == 0


async def test_la_cola_de_espera_tambien_esta_acotada() -> None:
    bulkhead = Bulkhead(max_concurrent=1, max_waiting=1, acquire_timeout_seconds=5)
    release, tasks = await occupy(bulkhead, 1)

    async def waiter() -> None:
        async with bulkhead.slot():
            pass

    waiting_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)

    with pytest.raises(BulkheadFullError):
        async with bulkhead.slot():
            pass

    release.set()
    await asyncio.gather(*tasks, waiting_task)


async def test_esperar_mas_del_timeout_por_un_slot_rechaza() -> None:
    bulkhead = Bulkhead(max_concurrent=1, max_waiting=5, acquire_timeout_seconds=0.05)
    release, tasks = await occupy(bulkhead, 1)

    with pytest.raises(BulkheadFullError, match="espera"):
        async with bulkhead.slot():
            pass

    assert bulkhead.waiting == 0
    release.set()
    await asyncio.gather(*tasks)


async def test_libera_el_slot_aunque_la_llamada_falle() -> None:
    bulkhead = Bulkhead(max_concurrent=1, max_waiting=0, acquire_timeout_seconds=1)

    with pytest.raises(RuntimeError):
        async with bulkhead.slot():
            raise RuntimeError("boom")

    assert bulkhead.active == 0
    async with bulkhead.slot():
        pass


@pytest.mark.parametrize(
    ("max_concurrent", "max_waiting", "timeout"),
    [(0, 1, 1), (1, -1, 1), (1, 1, -1)],
)
def test_valida_los_parametros(max_concurrent: int, max_waiting: int, timeout: float) -> None:
    with pytest.raises(ValueError):
        Bulkhead(
            max_concurrent=max_concurrent,
            max_waiting=max_waiting,
            acquire_timeout_seconds=timeout,
        )
