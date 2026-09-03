# Yuwen mini-program tabBar icons (81x81 PNG, line style)
# Usage: pwsh -File scripts/tools/make_tabbar_icons.ps1
Add-Type -AssemblyName System.Drawing

$outDir = 'E:\deepseek hanress\badminton-rag\mp\src\assets\tabbar'
New-Item -ItemType Directory -Force $outDir | Out-Null

$SIZE = 81
$GRAY = [System.Drawing.Color]::FromArgb(255, 124, 139, 130)   # #7c8b82
$GREEN = [System.Drawing.Color]::FromArgb(255, 4, 120, 87)     # #047857

function New-IconCanvas {
    $bmp = New-Object System.Drawing.Bitmap($SIZE, $SIZE, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    return @($g, $bmp)
}

function Save-Icon {
    param($g, $bmp, [string]$name)
    $g.Dispose()
    $bmp.Save((Join-Path $outDir $name), [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}

function New-Pen {
    param($color)
    $p = New-Object System.Drawing.Pen($color, 6)
    $p.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $p.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $p.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    return $p
}

function RoundRect-Path {
    param([float]$x, [float]$y, [float]$w, [float]$h, [float]$r)
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $p.AddArc($x, $y, 2 * $r, 2 * $r, 180, 90)
    $p.AddArc(($x + $w - 2 * $r), $y, 2 * $r, 2 * $r, 270, 90)
    $p.AddArc(($x + $w - 2 * $r), ($y + $h - 2 * $r), 2 * $r, 2 * $r, 0, 90)
    $p.AddArc($x, ($y + $h - 2 * $r), 2 * $r, 2 * $r, 90, 90)
    $p.CloseFigure()
    return $p
}

function Draw-Chat {
    param($g, $pen, $color)
    $bubble = RoundRect-Path 13 14 55 42 15
    $g.DrawPath($pen, $bubble)
    $g.DrawLine($pen, 30, 53, 24, 64)
    $g.DrawLine($pen, 24, 64, 42, 55)
    $dot = New-Object System.Drawing.SolidBrush($color)
    $g.FillEllipse($dot, 26, 32, 8, 8)
    $g.FillEllipse($dot, 37, 32, 8, 8)
    $g.FillEllipse($dot, 48, 32, 8, 8)
}

function Draw-Discover {
    param($g, $pen, $color)
    $g.DrawEllipse($pen, 14, 14, 53, 53)
    $needle = New-Object System.Drawing.Drawing2D.GraphicsPath
    $needle.AddPolygon(@(
        [System.Drawing.PointF]::new(40.5, 25),
        [System.Drawing.PointF]::new(52, 40.5),
        [System.Drawing.PointF]::new(40.5, 56),
        [System.Drawing.PointF]::new(29, 40.5)
    ))
    $g.FillPath((New-Object System.Drawing.SolidBrush($color)), $needle)
}

function Draw-Workbench {
    param($g, $pen, $color)
    $body = RoundRect-Path 12 29 57 33 10
    $g.DrawPath($pen, $body)
    $g.DrawLine($pen, 29, 29, 29, 22)
    $g.DrawLine($pen, 29, 22, 52, 22)
    $g.DrawLine($pen, 52, 22, 52, 29)
    $g.DrawEllipse($pen, 36.5, 39, 8, 8)
}

function Draw-Profile {
    param($g, $pen, $color)
    $g.DrawEllipse($pen, 27, 14, 27, 27)
    $arcRect = [System.Drawing.Rectangle]::new(14, 37, 53, 46)
    $g.DrawArc($pen, $arcRect, 180, 180)
}

foreach ($pair in @(
    @{ name = 'chat.png';            color = $GRAY },
    @{ name = 'chat-active.png';     color = $GREEN },
    @{ name = 'discover.png';        color = $GRAY },
    @{ name = 'discover-active.png'; color = $GREEN },
    @{ name = 'workbench.png';       color = $GRAY },
    @{ name = 'workbench-active.png'; color = $GREEN },
    @{ name = 'profile.png';         color = $GRAY },
    @{ name = 'profile-active.png';  color = $GREEN }
)) {
    $c = New-IconCanvas
    $g = $c[0]; $bmp = $c[1]
    $pen = New-Pen $pair.color
    if ($pair.name -like 'chat*') { Draw-Chat $g $pen $pair.color }
    elseif ($pair.name -like 'discover*') { Draw-Discover $g $pen $pair.color }
    elseif ($pair.name -like 'workbench*') { Draw-Workbench $g $pen $pair.color }
    else { Draw-Profile $g $pen $pair.color }
    Save-Icon $g $bmp $pair.name
    Write-Host "ok: $($pair.name)"
}
