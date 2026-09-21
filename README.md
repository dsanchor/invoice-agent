# Invoice Agent

Servicio Python que publica un agente de facturas de Microsoft Agent Framework mediante A2A JSON-RPC. Usa un proveedor compatible con la API de OpenAI y descubre sus tools en un servidor MCP remoto.

## Configuracion

1. Copia `.env.example` a `.env`.
2. Configura `OPENAI_MODEL` y la URL compatible con OpenAI publicada por APIM en `OPENAI_BASE_URL`.
3. Configura en `OPENAI_TOKEN_SCOPE` el scope de la API protegida por APIM, por ejemplo `api://<application-client-id>/.default`.
4. Configura en `MCP_SERVER_URL` el endpoint Streamable HTTP del servidor MCP, por ejemplo `https://mcp.example.com/mcp`.
5. Configura en `MCP_TOKEN_SCOPE` el scope de la API MCP protegida, por ejemplo `api://<mcp-application-client-id>/.default`.
6. Inicia sesion con `az login` para desarrollo local.

El agente obtiene los JWT mediante `DefaultAzureCredential`. En Azure Container Apps, habilita una identidad administrada y asignale los roles o permisos de aplicacion requeridos por las APIs de OpenAI y MCP. No se necesita `OPENAI_API_KEY`.

El agente envia `Authorization: Bearer <token>` al servidor MCP. Conecta al arrancar, ejecuta `tools/list` y registra las tools remotas; si el MCP no esta disponible o rechaza el token, el arranque falla.

`AGENT_PUBLIC_URL` debe ser la URL publica anunciada a los clientes. Cuando APIM este delante del agente, usa por ejemplo `https://<apim>.azure-api.net/a2a/invoice/`.

Los limites de espera y de llamadas se pueden ajustar con:

- `OPENAI_TIMEOUT_SECONDS` (30): timeout de cada llamada al modelo.
- `OPENAI_MAX_RETRIES` (0): reintentos internos del SDK ante 429 y otros errores transitorios.
- `MCP_TIMEOUT_SECONDS` (15): timeout de cada llamada MCP.
- `AGENT_TIMEOUT_SECONDS` (45): limite total de una ejecucion A2A.
- `AGENT_MAX_MODEL_ROUNDTRIPS` (6): maximo de rondas modelo/tools.
- `AGENT_MAX_TOOL_CALLS` (6): maximo total de llamadas a tools.
- `AGENT_MAX_CONSECUTIVE_TOOL_ERRORS` (1): errores consecutivos de tools antes de abandonar el bucle.

Al alcanzar un timeout, recibir un 429 del modelo o fallar una conexion upstream, la tarea A2A termina en estado `failed` con un mensaje apto para el cliente. Los detalles tecnicos permanecen en los logs.

## Ejecucion local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Endpoints:

- `GET /health`
- `GET /.well-known/agent-card.json`
- `POST /` para A2A JSON-RPC

El endpoint acepta opcionalmente los headers `userId` y `upn`. Cuando estan presentes, el agente los reenvia como headers HTTP de la llamada al endpoint OpenAI mediante las opciones de cada ejecucion; no se incluyen en los mensajes del prompt. `userId` debe ser un GUID valido y `upn` no puede superar 320 caracteres; valores invalidos se ignoran.

En produccion, APIM debe eliminar o sobrescribir ambos headers despues de validar el JWT. El agente no debe confiar en valores enviados directamente por el cliente:

```xml
<set-header name="userId" exists-action="override">
  <value>@(((Jwt)context.Variables["jwt"]).Claims["oid"].FirstOrDefault())</value>
</set-header>
<set-header name="upn" exists-action="override">
  <value>@(((Jwt)context.Variables["jwt"]).Claims["upn"].FirstOrDefault())</value>
</set-header>
```

Test:

```bash
curl -X POST http://localhost:8081/ \
  -H 'Content-Type: application/json' \
  -H 'A2A-Version: 1.0' \
  -H 'userId: 85fe7f9c-91d0-4a87-a761-1d46d7aff925' \
  -H 'upn: user@contoso.com' \
  --data-binary '{"jsonrpc":"2.0","id":"1","method":"SendMessage","params":{"message":{"role":"ROLE_USER","messageId":"msg-1","parts":[{"text":"Show me all invoices for Contoso"}]}}}'
```

## Docker

```bash
docker build -t invoice-agent:local .
docker run --rm -p 8080:8080 \
  --env-file .env \
  invoice-agent:local
```

La autenticacion interactiva local no se transmite automaticamente al contenedor. Para pruebas en Docker proporciona una credencial admitida por `DefaultAzureCredential`; en Container Apps utiliza identidad administrada.

## Despliegue manual en Azure Container Apps

Estos pasos crean la aplicacion en un Container Apps Environment existente usando la imagen publicada en GHCR. Define primero los valores del entorno:

```bash
az login
az extension add --name containerapp --upgrade

SUBSCRIPTION_ID="<subscription-id>"
RESOURCE_GROUP="<resource-group>"
CONTAINERAPPS_ENVIRONMENT="<container-apps-environment>"
CONTAINER_APP_NAME="invoice-agent"
IMAGE="ghcr.io/dsanchor/invoice-agent:latest"
OPENAI_MODEL="<model-name>"
OPENAI_BASE_URL="https://<apim-name>.azure-api.net/<openai-path>/v1"
OPENAI_TOKEN_SCOPE="api://<application-client-id>/.default"
MCP_SERVER_URL="https://<mcp-host>/mcp"
MCP_TOKEN_SCOPE="api://<mcp-application-client-id>/.default"

az account set --subscription "$SUBSCRIPTION_ID"
ENVIRONMENT_ID=$(az containerapp env show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINERAPPS_ENVIRONMENT" \
  --query id \
  --output tsv)
```

Crea la Container App con ingress externo, identidad administrada y el puerto del contenedor:

```bash
az containerapp create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --environment "$ENVIRONMENT_ID" \
  --image "$IMAGE" \
  --ingress external \
  --target-port 8080 \
  --system-assigned \
  --env-vars \
    PORT=8080 \
    LOG_LEVEL=INFO \
    OPENAI_MODEL="$OPENAI_MODEL" \
    OPENAI_BASE_URL="$OPENAI_BASE_URL" \
    OPENAI_TOKEN_SCOPE="$OPENAI_TOKEN_SCOPE" \
    MCP_SERVER_URL="$MCP_SERVER_URL" \
    MCP_TOKEN_SCOPE="$MCP_TOKEN_SCOPE"
```

Obtiene la URL asignada y actualiza la URL publica anunciada en el Agent Card:

```bash
FQDN=$(az containerapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

az containerapp update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --set-env-vars AGENT_PUBLIC_URL="https://$FQDN/"

curl --fail "https://$FQDN/health"
curl --fail "https://$FQDN/.well-known/agent-card.json"
```

La identidad administrada debe tener los permisos de aplicacion que las APIs validan para `OPENAI_TOKEN_SCOPE` y `MCP_TOKEN_SCOPE`. Puedes obtener su principal ID con:

```bash
az containerapp identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_APP_NAME" \
  --query principalId \
  --output tsv
```

## APIM y produccion

Este servicio no autentica peticiones A2A. Configura APIM para validar JWT de Entra ID, aplicar autorizacion, limites de consumo y correlacion antes de reenviar al Container App. Restringe el ingress para que el backend no pueda eludir APIM.

El almacenamiento de tareas y sesiones es en memoria, adecuado solo para la demo con una replica. Antes de escalar a varias replicas se debe incorporar almacenamiento persistente y vincular la propiedad de tareas y sesiones a una identidad autenticada, no a los IDs recibidos en el protocolo A2A.