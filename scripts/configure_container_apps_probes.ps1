[CmdletBinding()]
param(
    [Parameter()]
    [string]$ResourceGroup = "grupoderecursos_cassiofrd"
)

$ErrorActionPreference = "Stop"

$apps = @(
    @{ Name = "inventory-agent"; Container = "inventory-agent"; Port = 8001 },
    @{ Name = "supplier-agent"; Container = "supplier-agent"; Port = 8002 },
    @{ Name = "supervisor-agent"; Container = "supervisor-agent"; Port = 8000 }
)

$apiVersions = az provider show `
    --namespace Microsoft.App `
    --query "resourceTypes[?resourceType=='containerApps'].apiVersions[]" `
    --output tsv

$apiVersion = $apiVersions |
    Where-Object { $_ -and $_ -notmatch "preview" } |
    Sort-Object -Descending |
    Select-Object -First 1

if (-not $apiVersion) {
    throw "Could not resolve a stable Microsoft.App/containerApps API version."
}

foreach ($app in $apps) {
    Write-Host "Configuring health probes for $($app.Name)..." -ForegroundColor Cyan

    $resourceJson = az containerapp show `
        --name $app.Name `
        --resource-group $ResourceGroup `
        --output json

    if ($LASTEXITCODE -ne 0) {
        throw "Could not read Container App $($app.Name)."
    }

    $resource = $resourceJson | ConvertFrom-Json
    $container = $resource.properties.template.containers |
        Where-Object { $_.name -eq $app.Container } |
        Select-Object -First 1

    if (-not $container) {
        throw "Container '$($app.Container)' was not found in $($app.Name)."
    }

    $container | Add-Member `
        -NotePropertyName probes `
        -NotePropertyValue @(
            @{
                type = "Startup"
                httpGet = @{
                    path = "/live"
                    port = $app.Port
                    scheme = "HTTP"
                }
                initialDelaySeconds = 2
                periodSeconds = 5
                timeoutSeconds = 3
                failureThreshold = 10
                successThreshold = 1
            },
            @{
                type = "Liveness"
                httpGet = @{
                    path = "/live"
                    port = $app.Port
                    scheme = "HTTP"
                }
                initialDelaySeconds = 10
                periodSeconds = 30
                timeoutSeconds = 5
                failureThreshold = 3
                successThreshold = 1
            },
            @{
                type = "Readiness"
                httpGet = @{
                    path = "/ready"
                    port = $app.Port
                    scheme = "HTTP"
                }
                initialDelaySeconds = 5
                periodSeconds = 10
                timeoutSeconds = 5
                failureThreshold = 3
                successThreshold = 1
            }
        ) `
        -Force

    $body = @{
        properties = @{
            template = $resource.properties.template
        }
    } | ConvertTo-Json -Depth 100 -Compress

    $uri = "https://management.azure.com$($resource.id)?api-version=$apiVersion"

    az rest `
        --method patch `
        --uri $uri `
        --headers "Content-Type=application/json" `
        --body $body `
        --output none

    if ($LASTEXITCODE -ne 0) {
        throw "Could not configure probes for $($app.Name)."
    }
}

Write-Host "`nConfigured probes:" -ForegroundColor Green

foreach ($app in $apps) {
    az containerapp show `
        --name $app.Name `
        --resource-group $ResourceGroup `
        --query "properties.template.containers[0].probes[].{Type:type,Path:httpGet.path,Port:httpGet.port,Period:periodSeconds}" `
        --output table
}
