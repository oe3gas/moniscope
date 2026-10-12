# EB200_Sources2Text.ps1
#
# Generates EB200_sources.txt for upload to the Claude project knowledge base.
# Run from the repository root (e:\eb200\eb200repo):
#
#   .\EB200_Sources2Text.ps1
#
# Optional parameters:
#   -OutputFile   path to output file    (default: EB200_sources.txt)
#   -SkipEmpty    skip __init__.py files that contain only comments/whitespace
#   -SkipTests    skip files in tests/ directories
#
# Files included:
#   **/*.py   All Python source files found anywhere under the repo root
#             (venv/, .venv/, __pycache__/, .git/ are always excluded)
#
# The output file is UTF-8 without BOM, LF line endings, no duplicate files.

param(
    [string] $OutputFile = "EB200_sources.txt",
    [switch] $SkipEmpty  = $true,
    [switch] $SkipTests  = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Header: repo info and timestamp
# ---------------------------------------------------------------------------

$repoRoot  = (Get-Location).Path.TrimEnd()
$timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " UTC+0"

# Try to get git info (gracefully skip if git not available / not a repo)
$gitBranch = ""
$gitHash   = ""
try {
    $gitBranch = & git rev-parse --abbrev-ref HEAD 2>$null
    $gitHash   = & git rev-parse --short HEAD       2>$null
} catch { }

$header = @"
# =============================================================================
# EB200 - Python source export for Claude project knowledge
# Generated : $timestamp
# Repo root : $repoRoot
$(if ($gitBranch) { "# Branch    : $gitBranch  ($gitHash)" })
# =============================================================================
#
# Contains all *.py files found recursively under the repository root
# (eb200_core/, workstream scripts, tools, etc.). Excluded directories:
#   .git, .venv, venv, env, __pycache__, .mypy_cache, .pytest_cache, build, dist
#
# Each file is preceded by a separator line:
#   # === <relative path> ===
# =============================================================================

"@

# ---------------------------------------------------------------------------
# Helper: canonical repo-relative path for a file (forward slashes).
# ---------------------------------------------------------------------------

function Get-RelPath {
    param([string] $AbsPath)

    $absNorm  = $AbsPath.Replace("\", "/")
    $rootNorm = $repoRoot.Replace("\", "/").TrimEnd("/")

    if ($absNorm.StartsWith($rootNorm + "/", [System.StringComparison]::OrdinalIgnoreCase)) {
        return $absNorm.Substring($rootNorm.Length + 1)
    }
    # Fallback: keep a recognisable tail if the file is somehow outside the root.
    return $absNorm
}

# ---------------------------------------------------------------------------
# Directories to always exclude from the scan (noise, not project source)
# ---------------------------------------------------------------------------

$excludedDirPattern = "[\\/](\.git|\.venv|venv|env|__pycache__|\.mypy_cache|\.pytest_cache|build|dist)[\\/]"

# ---------------------------------------------------------------------------
# Collect Python source files - all *.py anywhere under the repo root
# ---------------------------------------------------------------------------

$priorityOrder = @(
    "config.py",
    "main.py",
    "__main__.py"
)

$pyFilesRaw = Get-ChildItem -Path $repoRoot -Recurse -Filter "*.py" -File |
    Where-Object { $_.FullName -notmatch $excludedDirPattern }

$pyFiles = $pyFilesRaw |
    Where-Object {
        # Optionally skip test files
        if ($SkipTests -and $_.FullName -match "[\\/]tests[\\/]") { return $false }
        return $true
    } |
    Sort-Object {
        $rel   = Get-RelPath $_.FullName
        $depth = ($rel.Split("/", [System.StringSplitOptions]::None)).Count
        $prio  = 99
        for ($i = 0; $i -lt $priorityOrder.Count; $i++) {
            if ($_.Name -eq $priorityOrder[$i]) { $prio = $i; break }
        }
        "{0:D2}_{1:D2}_{2}" -f $depth, $prio, $rel
    }

# ---------------------------------------------------------------------------
# Deduplicate using canonical relative path, then write output
# ---------------------------------------------------------------------------

$seen  = @{}
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add($header)

$included = 0
$skipped  = 0

foreach ($file in $pyFiles) {
    $absPath = $file.FullName
    $relPath = Get-RelPath $absPath

    if ($seen.ContainsKey($relPath)) {
        $skipped++
        continue
    }
    $seen[$relPath] = $true

    $content = (Get-Content $absPath -Raw -Encoding UTF8) -replace "`r`n", "`n" -replace "`r", "`n"

    if ($SkipEmpty -and $file.Name -eq "__init__.py") {
        $stripped = $content -replace "#[^\n]*", "" -replace "\s", ""
        if ($stripped.Length -eq 0) {
            $skipped++
            continue
        }
    }

    $lines.Add("# === $relPath ===")
    $lines.Add($content)
    $lines.Add("")
    $included++
}

# ---------------------------------------------------------------------------
# Footer: summary
# ---------------------------------------------------------------------------

$footer = @"
# =============================================================================
# End of export
# Files included : $included
# Files skipped  : $skipped  (duplicates / empty __init__.py / tests)
# =============================================================================
"@
$lines.Add($footer)

# ---------------------------------------------------------------------------
# Write output - UTF-8 without BOM, LF line endings
# ---------------------------------------------------------------------------

$outputPath = Join-Path $repoRoot $OutputFile
$utf8NoBom  = [System.Text.UTF8Encoding]::new($false)

[System.IO.File]::WriteAllText(
    $outputPath,
    ($lines -join "`n"),
    $utf8NoBom
)

Write-Host "OK  Written to: $outputPath"
Write-Host "    Python files found: $(([array]$pyFiles).Count)"
Write-Host "    Included:           $included files"
Write-Host "    Skipped:            $skipped files"