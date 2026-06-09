param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DistDir = (Join-Path $ProjectRoot "dist")
)

$ErrorActionPreference = "Stop"

# ffplay.exe dropped (unused by the app; ~130 MB per artifact).
$ffmpegFileNames = @("ffmpeg.exe", "ffprobe.exe")
$searchRoots = New-Object System.Collections.Generic.List[string]
$seenRoots = @{}
$preferredDesktopRoot = Join-Path $HOME "Desktop\ffmpeg"

function Add-SearchRoot {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $key = $resolved.ToLowerInvariant()
    if (-not $seenRoots.ContainsKey($key)) {
        $seenRoots[$key] = $true
        $searchRoots.Add($resolved) | Out-Null
    }

    if ((Split-Path -Leaf $resolved).ToLowerInvariant() -eq "bin") {
        Add-SearchRoot -Path (Split-Path -Parent $resolved)
    }
}

Add-SearchRoot -Path $preferredDesktopRoot
Add-SearchRoot -Path $env:JELLYRIP_FFMPEG_DIR
Add-SearchRoot -Path $env:FFMPEG_DIR
Add-SearchRoot -Path (Join-Path $ProjectRoot "ffmpeg")
Add-SearchRoot -Path (Join-Path $ProjectRoot "ffmpeg\bin")
Add-SearchRoot -Path (Join-Path (Split-Path -Parent $ProjectRoot) "ffmpeg")
Add-SearchRoot -Path (Join-Path (Split-Path -Parent $ProjectRoot) "ffmpeg\bin")

function Find-BundleFile {
    param([string]$FileName)
    foreach ($root in $searchRoots) {
        $directCandidates = @(
            (Join-Path $root $FileName),
            (Join-Path $root (Join-Path "bin" $FileName))
        )
        foreach ($candidate in $directCandidates) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        }

        $found = Get-ChildItem -LiteralPath $root -Recurse -File -Filter $FileName -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            return $found.FullName
        }
    }
    throw "Could not find $FileName in configured FFmpeg search roots."
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

foreach ($fileName in $ffmpegFileNames) {
    $source = Find-BundleFile -FileName $fileName
    Copy-Item -LiteralPath $source -Destination (Join-Path $DistDir $fileName) -Force
}

$ffmpegExe = Join-Path $DistDir "ffmpeg.exe"
$quotedFfmpegExe = '"' + $ffmpegExe + '"'
$versionOutput = & cmd /c "$quotedFfmpegExe -version 2>&1" | Out-String
if ($versionOutput -notmatch "--enable-gpl" -or $versionOutput -notmatch "--enable-version3") {
    throw "The staged FFmpeg build is not the expected GPLv3-capable build."
}

$licenseOutput = & cmd /c "$quotedFfmpegExe -L 2>&1" | Out-String
if ($licenseOutput -notmatch "GNU General Public License") {
    throw "The staged FFmpeg build did not report the GNU GPL license."
}

Copy-Item -LiteralPath (Find-BundleFile -FileName "LICENSE") -Destination (Join-Path $DistDir "FFmpeg-LICENSE.txt") -Force
Copy-Item -LiteralPath (Find-BundleFile -FileName "README.txt") -Destination (Join-Path $DistDir "FFmpeg-README.txt") -Force

Write-Output "Staged FFmpeg bundle and notices in $DistDir"
