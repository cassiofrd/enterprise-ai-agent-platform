[CmdletBinding()]
param(
    [Parameter()]
    [string]$ResourceGroup = "grupoderecursos_cassiofrd",

    [Parameter()]
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

if (-not $Apply) {
    throw "No changes were made. Re-run with -Apply after reading the script and preparing API_TOKEN."
}

if (-not $env:API_TOKEN) {
    throw "Set a strong API_TOKEN in the current shell before running this script."
}

$apps = @("inventory-agent", "supplier-agent", "supervisor-agent")

foreach ($app in $apps) {
    az containerapp secret set `
        --name $app `
        --resource-group $ResourceGroup `
        --secrets "internal-api-token=$env:API_TOKEN" `
        --output none

    az containerapp update `
        --name $app `
        --resource-group $ResourceGroup `
        --set-env-vars API_TOKEN=secretref:internal-api-token `
        --output none
}

$inventoryFqdn = az containerapp show `
    --name inventory-agent `
    --resource-group $ResourceGroup `
    --query properties.configuration.ingress.fqdn `
    --output tsv

$supplierFqdn = az containerapp show `
    --name supplier-agent `
    --resource-group $ResourceGroup `
    --query properties.configuration.ingress.fqdn `
    --output tsv

az containerapp update `
    --name supervisor-agent `
    --resource-group $ResourceGroup `
    --set-env-vars `
        INVENTORY_AGENT_URL="https://$inventoryFqdn/invoke" `
        SUPPLIER_AGENT_URL="https://$supplierFqdn/invoke" `
    --output none

az containerapp ingress update `
    --name inventory-agent `
    --resource-group $ResourceGroup `
    --type internal `
    --target-port 8001 `
    --transport auto `
    --allow-insecure false `
    --output none

az containerapp ingress update `
    --name supplier-agent `
    --resource-group $ResourceGroup `
    --type internal `
    --target-port 8002 `
    --transport auto `
    --allow-insecure false `
    --output none

Write-Host "Inventory and Supplier now use internal ingress." -ForegroundColor Green
Write-Host "Supervisor remains externally accessible."
Write-Host "Keep API_TOKEN available to clients that call protected Supervisor endpoints."
