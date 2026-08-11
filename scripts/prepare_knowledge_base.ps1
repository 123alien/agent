$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceDir = Join-Path $projectRoot "docs\knowledge_base\official_sources"
$outputDir = Join-Path $projectRoot "docs\knowledge_base\dify_upload"

if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
    throw "知识库源文件目录不存在: $sourceDir"
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

Get-ChildItem -LiteralPath $sourceDir -File | ForEach-Object {
    if ($_.Extension -ieq ".pdf") {
        Copy-Item -LiteralPath $_.FullName -Destination $outputDir -Force
        return
    }
    if ($_.Extension -ine ".html") {
        return
    }

    $html = Get-Content -LiteralPath $_.FullName -Raw
    $html = [regex]::Replace(
        $html,
        "(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>",
        " "
    )
    $html = [regex]::Replace($html, "(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "`n")
    $text = [regex]::Replace($html, "(?s)<[^>]+>", " ")
    $text = [System.Net.WebUtility]::HtmlDecode($text)
    $text = [regex]::Replace($text, "[\t ]+", " ")
    $text = [regex]::Replace(
        $text,
        "第\s+([一二三四五六七八九十百千万零〇0-9]+)\s*(条|章|节)",
        '第$1$2'
    )
    $text = [regex]::Replace($text, "(?m)^\s+$", "")
    $text = [regex]::Replace($text, "\r?\n(?:\s*\r?\n){2,}", "`r`n`r`n")
    $text = $text.Trim()

    $outputPath = Join-Path $outputDir ($_.BaseName + ".txt")
    Set-Content -LiteralPath $outputPath -Value $text -Encoding utf8
}

Get-ChildItem -LiteralPath $outputDir -File | Sort-Object Name |
    Select-Object Name, Length
