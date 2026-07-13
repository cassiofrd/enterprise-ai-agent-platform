[CmdletBinding()]
param(
    [Parameter()]
    [string]$ResourceGroup = "grupoderecursos_cassiofrd",

    [Parameter()]
    [string]$Location = "brazilsouth",

    [Parameter()]
    [string]$WorkspaceName = "workspace-grupoderecursoscassiofrdryBL",

    [Parameter()]
    [string]$ApplicationInsightsName = "appi-enterprise-ai-agent-platform"
)

$ErrorActionPreference = "Stop"

az extension add `
    --name application-insights `
    --upgrade `
    --only-show-errors

$subscriptionId = az account show --query id --output tsv
$workspaceId = "/subscriptions/$subscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.OperationalInsights/workspaces/$WorkspaceName"

$existing = az monitor app-insights component show `
    --app $ApplicationInsightsName `
    --resource-group $ResourceGroup `
    --query name `
    --output tsv `
    2>$null

if (-not $existing) {
    Write-Host "Creating workspace-based Application Insights..." -ForegroundColor Cyan

    az monitor app-insights component create `
        --app $ApplicationInsightsName `
        --location $Location `
        --resource-group $ResourceGroup `
        --kind web `
        --application-type web `
        --workspace $workspaceId `
        --output none
}

$connectionString = az monitor app-insights component show `
    --app $ApplicationInsightsName `
    --resource-group $ResourceGroup `
    --query connectionString `
    --output tsv

if (-not $connectionString) {
    throw "Application Insights connection string was not returned."
}

$apps = @(
    @{ Name = "inventory-agent"; ServiceName = "enterprise-ai-agent-inventory" },
    @{ Name = "supplier-agent"; ServiceName = "enterprise-ai-agent-supplier" },
    @{ Name = "supervisor-agent"; ServiceName = "enterprise-ai-agent-supervisor" }
)

foreach ($app in $apps) {
    Write-Host "Enabling Azure Monitor export for $($app.Name)..." -ForegroundColor Cyan

    az containerapp secret set `
        --name $app.Name `
        --resource-group $ResourceGroup `
        --secrets "appinsights-connection-string=$connectionString" `
        --output none

    az containerapp update `
        --name $app.Name `
        --resource-group $ResourceGroup `
        --set-env-vars `
            OTEL_ENABLED=true `
            OTEL_SERVICE_NAME=$($app.ServiceName) `
            APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-connection-string `
        --output none
}

Write-Host "`nApplication Insights configured without printing its connection string." -ForegroundColor Green
Write-Host "Resource: $ApplicationInsightsName"
