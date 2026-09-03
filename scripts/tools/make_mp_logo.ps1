# Yuwen (YuWen) mini-program logo generator (cartoon style)
# Usage: pwsh -File scripts/tools/make_mp_logo.ps1
Add-Type -AssemblyName System.Drawing

$outDir = 'E:\deepseek hanress\badminton-rag\mp\src\assets'
$SIZE = 1024

function New-Canvas {
    param([int]$size)
    $bmp = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    return @($g, $bmp)
}

# 4-point star path
function Star-Path {
    param([float]$x, [float]$y, [float]$r)
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $k = 0.30 * $r
    $pts = @(
        [System.Drawing.PointF]::new([float]$x, [float]($y - $r)),
        [System.Drawing.PointF]::new([float]($x + $k), [float]($y - $k)),
        [System.Drawing.PointF]::new([float]($x + $r), [float]$y),
        [System.Drawing.PointF]::new([float]($x + $k), [float]($y + $k)),
        [System.Drawing.PointF]::new([float]$x, [float]($y + $r)),
        [System.Drawing.PointF]::new([float]($x - $k), [float]($y + $k)),
        [System.Drawing.PointF]::new([float]($x - $r), [float]$y),
        [System.Drawing.PointF]::new([float]($x - $k), [float]($y - $k))
    )
    $p.AddPolygon($pts)
    return $p
}

# Tapered feather leaf pointing up: pivot at origin, tip at (0, -len)
function Leaf-Path {
    param([float]$len, [float]$wid)
    $p = New-Object System.Drawing.Drawing2D.GraphicsPath
    $p.AddBezier(0, 0, (-0.50 * $wid), (-0.30 * $len), (-0.45 * $wid), (-0.72 * $len), 0, -$len)
    $p.AddBezier(0, -$len, (0.45 * $wid), (-0.72 * $len), (0.50 * $wid), (-0.30 * $len), 0, 0)
    $p.CloseFigure()
    return $p
}

# Draw one feather leaf rotated around the pivot
function Draw-Leaf {
    param([float]$deg, $path, $brush, $pen)
    $stSave = $g.Save()
    $g.TranslateTransform(0, 30)   # leaf pivot
    $g.RotateTransform($deg)
    $g.FillPath($brush, $path)
    $g.DrawPath($pen, $path)
    $g.Restore($stSave)
}

$canvas = New-Canvas $SIZE
$g = $canvas[0]; $bmp = $canvas[1]

# ---------- background: green gradient ----------
$rect = [System.Drawing.Rectangle]::new(0, 0, $SIZE, $SIZE)
$grad = [System.Drawing.Drawing2D.LinearGradientBrush]::new($rect, [System.Drawing.Color]::FromArgb(255, 16, 185, 129), [System.Drawing.Color]::FromArgb(255, 4, 120, 87), 90.0)
$g.FillRectangle($grad, $rect)

# center soft glow
$glow = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(26, 255, 255, 255))
$g.FillEllipse($glow, 192, 130, 640, 640)

# ground shadow
$sh = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(45, 2, 60, 45))
$g.FillEllipse($sh, 342, 870, 340, 52)

# ---------- cartoon shuttlecock (local coords: origin = feather pivot, -y up, rotate -8 deg) ----------
$g.TranslateTransform(512, 500)
$g.RotateTransform(-8)

# back feather layer (11, darker cream)
$bBack = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 231, 223, 205))
$penBack = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 216, 207, 186), 2)
$leafBack = Leaf-Path 340 64
for ($i = -5; $i -le 5; $i++) { Draw-Leaf ($i * 10.0) $leafBack $bBack $penBack }

# front feather layer (7, lighter cream)
$bFront = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 248, 243, 230))
$penFront = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 232, 224, 206), 2)
$leafFront = Leaf-Path 278 58
for ($i = -3; $i -le 3; $i++) { Draw-Leaf ($i * 10.0) $leafFront $bFront $penFront }

# feather base cover (where leaves converge)
$bBase = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 240, 233, 214))
$g.FillEllipse($bBase, -62, -18, 124, 88)

# ---------- orange band ----------
$bBand = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 249, 115, 22))
$penCork = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 228, 219, 198), 4)
$r2 = 22
$bandPath = New-Object System.Drawing.Drawing2D.GraphicsPath
$bandPath.AddArc(-66, 34, 2 * $r2, 2 * $r2, 180, 90)
$bandPath.AddArc(66 - 2 * $r2, 34, 2 * $r2, 2 * $r2, 270, 90)
$bandPath.AddArc(66 - 2 * $r2, 98 - 2 * $r2, 2 * $r2, 2 * $r2, 0, 90)
$bandPath.AddArc(-66, 98 - 2 * $r2, 2 * $r2, 2 * $r2, 90, 90)
$bandPath.CloseFigure()
$g.FillPath($bBand, $bandPath)
$g.DrawPath($penCork, $bandPath)

# ---------- cork ----------
$bCork = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 255, 250, 238))
$r = 40
$corkPath = New-Object System.Drawing.Drawing2D.GraphicsPath
$corkPath.AddArc(-64, 98, 2 * $r, 2 * $r, 180, 90)
$corkPath.AddArc(64 - 2 * $r, 98, 2 * $r, 2 * $r, 270, 90)
$corkPath.AddArc(64 - 2 * $r, 256 - 2 * $r, 2 * $r, 2 * $r, 0, 90)
$corkPath.AddArc(-64, 256 - 2 * $r, 2 * $r, 2 * $r, 90, 90)
$corkPath.CloseFigure()
$g.FillPath($bCork, $corkPath)
$g.DrawPath($penCork, $corkPath)

# ---------- face on cork ----------
$bEye = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 52, 64, 71))
$g.FillEllipse($bEye, -44, 148, 28, 36)
$g.FillEllipse($bEye, 16, 148, 28, 36)
$bHi = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 255, 255, 255))
$g.FillEllipse($bHi, -38, 154, 10, 10)
$g.FillEllipse($bHi, 22, 154, 10, 10)

$penSmile = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 52, 64, 71), 6)
$penSmile.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
$penSmile.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
$g.DrawArc($penSmile, -32, 172, 64, 42, 15, 150)

$bBlush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(190, 252, 165, 165))
$g.FillEllipse($bBlush, -62, 190, 28, 15)
$g.FillEllipse($bBlush, 34, 190, 28, 15)

# glossy highlight on cork (top-right)
$bShine = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(120, 255, 255, 255))
$g.FillEllipse($bShine, 8, 120, 34, 24)

$g.ResetTransform()

# ---------- question-mark badge (top-right) ----------
$stSave = $g.Save()
$g.TranslateTransform(798, 198)
$g.RotateTransform(-10)
$bBadge = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(238, 255, 255, 255))
$g.FillEllipse($bBadge, -102, -102, 204, 204)
$g.Restore($stSave)

$font = [System.Drawing.Font]::new('Segoe UI', 134.0, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$bQ = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 13, 148, 136))
$stSave = $g.Save()
$g.TranslateTransform(798, 205)
$g.RotateTransform(-10)
$fmt = New-Object System.Drawing.StringFormat
$fmt.Alignment = [System.Drawing.StringAlignment]::Center
$fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
$qRect = [System.Drawing.RectangleF]::new(-102.0, -102.0, 204.0, 204.0)
$g.DrawString('?', $font, $bQ, $qRect, $fmt)
$g.Restore($stSave)

# ---------- sparkles ----------
$bStar = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(180, 255, 255, 255))
$g.FillPath($bStar, (Star-Path 228 265 24))
$g.FillPath($bStar, (Star-Path 806 636 20))
$g.FillPath($bStar, (Star-Path 302 802 18))
$bStar2 = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(220, 253, 230, 138))
$g.FillEllipse($bStar2, 190, 312, 22, 22)
$g.FillEllipse($bStar2, 788, 592, 18, 18)

$g.Dispose()
$bmp.Save((Join-Path $outDir 'logo.png'), [System.Drawing.Imaging.ImageFormat]::Png)

# thumbnails
foreach ($sz in @(512, 256)) {
    $s = New-Canvas $sz
    $sg = $s[0]; $sb = $s[1]
    $sg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $sg.DrawImage($bmp, 0, 0, $sz, $sz)
    $sg.Dispose()
    $sb.Save((Join-Path $outDir ("logo@{0}.png" -f $sz)), [System.Drawing.Imaging.ImageFormat]::Png)
    $sb.Dispose()
}
# circular-cropped preview 512 (avatar display is a circle)
$circ = New-Canvas 512
$cg = $circ[0]; $cb = $circ[1]
$cg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$cp = New-Object System.Drawing.Drawing2D.GraphicsPath
$cp.AddEllipse(0, 0, 512, 512)
$cg.SetClip($cp)
$cg.DrawImage($bmp, 0, 0, 512, 512)
$cg.Dispose()
$cb.Save((Join-Path $outDir 'logo_circle.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$cb.Dispose()

$bmp.Dispose()
Write-Host 'done: logo.png / logo@512.png / logo@256.png / logo_circle.png'
