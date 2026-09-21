import argparse
import asyncio
import os
import sys
import uuid
from collections.abc import Sequence

import httpx
from a2a.client import A2AClientError, ClientConfig, ClientFactory
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    TaskState,
)


TERMINAL_ERROR_STATES = {
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_AUTH_REQUIRED,
}


def parse_header(value: str) -> tuple[str, str]:
    separator = "=" if "=" in value else ":"
    if separator not in value:
        raise argparse.ArgumentTypeError(
            "Headers must use NAME=VALUE or NAME: VALUE."
        )

    name, header_value = value.split(separator, 1)
    name = name.strip()
    header_value = header_value.strip()
    if not name or not header_value:
        raise argparse.ArgumentTypeError("Header name and value cannot be empty.")
    if any(character in name + header_value for character in "\r\n"):
        raise argparse.ArgumentTypeError("Headers cannot contain line breaks.")
    return name, header_value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a message to an A2A agent and print streamed text."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Agent base URL used to resolve /.well-known/agent-card.json.",
    )
    parser.add_argument(
        "--agent-card-path",
        default="/.well-known/agent-card.json",
        help=(
            "Agent Card path relative to --url "
            "(default: /.well-known/agent-card.json)."
        ),
    )
    parser.add_argument(
        "--skip-agent-card",
        action="store_true",
        help=(
            "Do not download an Agent Card. Treat --url as a JSON-RPC endpoint "
            "that supports streaming."
        ),
    )
    parser.add_argument("--message", required=True, help="Message sent to the agent.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        type=parse_header,
        metavar="NAME=VALUE",
        help="HTTP header to send. Repeat this option for multiple headers.",
    )
    parser.add_argument(
        "--bearer-token-env",
        metavar="ENV_VAR",
        help=(
            "Read a bearer token from this environment variable and send it in "
            "the Authorization header."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60,
        help="HTTP timeout in seconds (default: 60).",
    )
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero.")
    if not args.agent_card_path.startswith("/"):
        parser.error("--agent-card-path must start with '/'.")
    if args.bearer_token_env:
        if any(name.lower() == "authorization" for name, _ in args.header):
            parser.error(
                "Use either --bearer-token-env or an Authorization --header, not both."
            )
        token = os.getenv(args.bearer_token_env, "").strip()
        if not token:
            parser.error(
                f"Environment variable {args.bearer_token_env!r} is not set or is empty."
            )
        args.header.append(("Authorization", f"Bearer {token}"))

    return args


def text_parts(parts: Sequence[Part]) -> str:
    return "".join(
        part.text for part in parts if part.WhichOneof("content") == "text"
    )


async def stream_message(args: argparse.Namespace) -> int:
    headers = dict(args.header)
    async with httpx.AsyncClient(
        headers=headers,
        timeout=args.timeout,
    ) as http_client:
        factory = ClientFactory(
            ClientConfig(
                streaming=True,
                httpx_client=http_client,
                accepted_output_modes=["text"],
            )
        )
        if args.skip_agent_card:
            card = AgentCard(
                name="Direct A2A agent",
                description="Agent configured directly by the streaming client.",
                version="unknown",
                default_input_modes=["text"],
                default_output_modes=["text"],
                capabilities=AgentCapabilities(streaming=True),
                supported_interfaces=[
                    AgentInterface(
                        url=args.url.rstrip("/") + "/",
                        protocol_binding="JSONRPC",
                    )
                ],
            )
            client = factory.create(card)
        else:
            client = await factory.create_from_url(
                args.url.rstrip("/"),
                relative_card_path=args.agent_card_path,
            )
        request = SendMessageRequest(
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_USER,
                parts=[Part(text=args.message)],
            ),
            configuration=SendMessageConfiguration(
                accepted_output_modes=["text"],
            ),
        )

        final_state = TaskState.TASK_STATE_UNSPECIFIED
        try:
            async for event in client.send_message(request):
                payload_type = event.WhichOneof("payload")

                if payload_type == "artifact_update":
                    text = text_parts(event.artifact_update.artifact.parts)
                    if text:
                        print(text, end="", flush=True)
                elif payload_type == "message":
                    text = text_parts(event.message.parts)
                    if text:
                        print(text, end="", flush=True)
                elif payload_type == "status_update":
                    status = event.status_update.status
                    final_state = status.state
                    state_name = TaskState.Name(status.state)
                    print(f"\n[{state_name}]", file=sys.stderr)
                    if status.HasField("message"):
                        status_message = text_parts(status.message.parts)
                        if status_message:
                            print(status_message, file=sys.stderr)
                elif payload_type == "task":
                    final_state = event.task.status.state
        finally:
            await client.close()

    if final_state in TERMINAL_ERROR_STATES:
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(stream_message(args))
    except (A2AClientError, httpx.HTTPError) as error:
        print(
            f"A2A request failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
