import logging
import os
import uuid
from asyncio import CancelledError, to_thread
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import httpx
import uvicorn
from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    TaskState,
)
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.openai import OpenAIChatClient
from agent_framework_hosting import AgentState
from agent_framework_hosting_a2a import a2a_from_run, a2a_to_run
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("invoice-agent")


INSTRUCTIONS = """You are an invoice specialist.
Use the available tools for every invoice lookup. Never invent invoice data.
If no invoice matches, say so clearly. Keep monetary amounts and identifiers exact.
"""


def caller_model_headers(context: RequestContext) -> dict[str, str]:
    headers = context.call_context.state.get("headers", {})
    user_id = headers.get("userid", "").strip()
    upn = headers.get("upn", "").strip()

    if user_id:
        try:
            user_id = str(uuid.UUID(user_id))
        except ValueError:
            logger.warning("Ignoring invalid userId header")
            user_id = ""
    if len(upn) > 320 or any(character in upn for character in "\r\n"):
        logger.warning("Ignoring invalid upn header")
        upn = ""
    return {
        key: value
        for key, value in (("userId", user_id), ("upn", upn))
        if value
    }


class AzureBearerAuth(httpx.Auth):
    def __init__(self, token_provider: Callable[[], str]) -> None:
        self.token_provider = token_provider

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, None]:
        token = await to_thread(self.token_provider)
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


class InvoiceAgentExecutor(AgentExecutor):
    def __init__(self, state: AgentState[Agent]) -> None:
        self.state = state

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.context_id is None:
            raise ValueError("A2A context id is required")
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id)
        await updater.cancel()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is None or context.context_id is None:
            raise ValueError("A2A message and context id are required")

        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, context.context_id)
        await updater.submit()
        try:
            await updater.start_work()
            run = a2a_to_run(context.message, stream=True, input_modes=["text"])
            model_headers = caller_model_headers(context)
            agent = await self.state.get_target()
            session_id = f"a2a:{context.tenant}:{context.context_id}"
            session = await self.state.get_or_create_session(session_id)
            stream = agent.run(
                run["messages"],
                session=session,
                options=run["options"],
                stream=True,
                client_kwargs=(
                    {"extra_headers": model_headers} if model_headers else None
                ),
            )

            default_artifact_id = uuid.uuid4().hex
            artifact_ids: set[str] = set()
            async for update in stream:
                parts = a2a_from_run(update, output_modes=["text"])
                if not parts:
                    continue
                artifact_id = update.message_id or default_artifact_id
                await updater.add_artifact(
                    parts=parts,
                    artifact_id=artifact_id,
                    append=True if artifact_id in artifact_ids else None,
                )
                artifact_ids.add(artifact_id)

            final_response = await stream.get_final_response()
            if not artifact_ids:
                parts = a2a_from_run(final_response, output_modes=["text"])
                if parts:
                    await updater.update_status(
                        state=TaskState.TASK_STATE_WORKING,
                        message=updater.new_agent_message(parts),
                    )
            await self.state.set_session(session_id, session)
            await updater.complete()
        except CancelledError:
            await updater.update_status(state=TaskState.TASK_STATE_CANCELED)
        except Exception:
            logger.exception("Invoice agent execution failed")
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=updater.new_agent_message(
                    [Part(text="Invoice agent execution failed.")]
                ),
            )


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_app() -> Starlette:
    model = os.environ["OPENAI_MODEL"]
    base_url = os.environ["OPENAI_BASE_URL"]
    openai_token_scope = os.environ["OPENAI_TOKEN_SCOPE"]
    mcp_server_url = os.environ["MCP_SERVER_URL"]
    mcp_token_scope = os.environ["MCP_TOKEN_SCOPE"]
    public_url = os.getenv("AGENT_PUBLIC_URL", "http://localhost:8080/").rstrip("/") + "/"

    credential = DefaultAzureCredential()
    openai_token_provider = get_bearer_token_provider(
        credential,
        openai_token_scope,
    )
    mcp_token_provider = get_bearer_token_provider(
        credential,
        mcp_token_scope,
    )

    async def token_provider() -> str:
        return await to_thread(openai_token_provider)

    client = OpenAIChatClient(
        model=model,
        api_key=token_provider,
        base_url=base_url,
    )
    mcp_http_client = httpx.AsyncClient(auth=AzureBearerAuth(mcp_token_provider))
    mcp_tool = MCPStreamableHTTPTool(
        name="invoice-mcp",
        url=mcp_server_url,
        http_client=mcp_http_client,
    )
    agent = Agent(
        client=client,
        name="InvoiceAgent",
        description="Queries invoice data by company, transaction ID, or invoice ID.",
        instructions=INSTRUCTIONS,
        tools=[mcp_tool],
    )

    @asynccontextmanager
    async def lifespan(_: Starlette):
        try:
            await mcp_tool.connect()
            yield
        finally:
            await mcp_tool.close()
            await mcp_http_client.aclose()
            credential.close()

    agent_card = AgentCard(
        name=agent.name,
        description=agent.description,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        supported_interfaces=[
            AgentInterface(url=public_url, protocol_binding="JSONRPC")
        ],
        skills=[
            AgentSkill(
                id="invoice-query",
                name="InvoiceQuery",
                description="Queries invoices by company, transaction ID, or invoice ID.",
                tags=["invoice", "agent-framework"],
                examples=["Show me all invoices for Contoso."],
            )
        ],
    )
    request_handler = DefaultRequestHandler(
        agent_executor=InvoiceAgentExecutor(AgentState(agent)),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            *create_agent_card_routes(agent_card),
            *create_jsonrpc_routes(request_handler, "/"),
        ]
    )


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
    )