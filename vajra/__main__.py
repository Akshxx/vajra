import asyncio
import signal
from contextlib import asynccontextmanager

import uvicorn

from vajra.agents.tribunal import get_tribunal_engine
from vajra.api.main import app
from vajra.config import settings
from vajra.core.causal_kg import get_fraud_graph
from vajra.core.database import close_db, init_db
from vajra.core.synthesis import get_policy_executor, get_policy_synthesizer


@asynccontextmanager
async def lifespan_manager():
    await init_db()
    await get_fraud_graph()
    await get_tribunal_engine()
    get_policy_synthesizer()
    get_policy_executor()
    yield
    await close_db()


async def run_server():
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8000,
        log_level=settings.LOG_LEVEL.lower(),
        lifespan="on",
    )
    server = uvicorn.Server(config)

    def handle_signal():
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    await server.serve()


if __name__ == "__main__":
    if settings.ENVIRONMENT == "production":
        asyncio.run(run_server())
    else:
        uvicorn.run(
            "vajra.api.main:app",
            host="0.0.0.0",
            port=8000,
            reload=settings.DEBUG,
            log_level=settings.LOG_LEVEL.lower(),
        )
