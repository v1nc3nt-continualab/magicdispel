# MagicDispel

在自己的电脑上清除照片里的隐私信息，画质不变。

**尚未发布的草稿：** 以下安装说明要等首个版本发布后才能使用。

[English](README.md)

```sh
magicdispel 照片.jpg
magicdispel "照片 1.heic" 截图.png
magicdispel --anonymous 照片.jpg
```

输入 `magicdispel` 和一个空格，把一张或多张照片拖进终端，按回车。
清理后的新照片保存在原图旁边，名为 `照片_clean.jpg`。原图不会被修改，也不会覆盖任何已有文件：
再次清理会依次生成 `_clean_1`、`_clean_2`……
使用 `--anonymous` 时，新文件改名为 `photo_<随机字符>.jpg`。它只改文件名，不会改动画面内容。

## 工作方式

MagicDispel 不是去找"已知的元数据"再删除，而是只拷贝显示图片所需的部分，重新写出一个新文件：
压缩后的像素、色彩配置、方向、DPI、透明度、动画时序、HDR 增益图等。其余内容一律不带过去：
定位、拍摄时间、相机与镜头信息、作者、注释、编辑软件、缩略图、深度图和人像蒙版、
C2PA 内容凭证，以及各种私有或未知的数据块，无论它们藏在文件的哪个位置。

- 图像数据逐字节原样拷贝。只有 BMP 会无损转存为 PNG。
- ICC 色彩配置保留颜色数据；其中的日期统一改为固定占位值 `2000-01-01`，
  描述改为 `Clean`，设备和创建者字段被清空。
- 每个结果保存前都要经过检查：格式模块独立重新解析结果；Pillow 解码出的像素和动画帧必须完全一致
  （HEIC 无法用 Pillow 解码，改为逐字节比对每个图像条目）；如果装了 ExifTool，它会再独立复查一遍。
  任何一项检查不通过，就不保存结果。
- 全程在本机完成：不上传图片，不收集遥测数据，不联网。

**这不是匿名工具。** 画面内容本身、与以前公开过的同一张照片的比对、分享时使用的账号，
都仍可能暴露人物和地点。删除深度图和风格数据后，之后再调整人像、景深和摄影风格的能力也会减少。
详见[隐私与格式说明](docs/PRIVACY.md)。

## 安装

需要 Python **3.10+**。[ExifTool](https://exiftool.org/) 是可选的：装了 12.73 或更新的版本，
MagicDispel 会用它对每个结果再复查一遍。

### macOS

已安装 [Homebrew](https://brew.sh/) 的用户执行：

```sh
brew install pipx
pipx ensurepath
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

可选的复查：`brew install exiftool`。

### Windows（PowerShell）

```powershell
winget install --exact --id Python.Python.3.12
```

重新打开 PowerShell，执行：

```powershell
py -3.12 -m pip install --user pipx
py -3.12 -m pipx ensurepath
py -3.12 -m pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

可选的复查：`winget install --exact --id OliverBetz.ExifTool`。

### Linux

Ubuntu 24.04+ 或较新的 Debian：

```sh
sudo apt install pipx
pipx ensurepath
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

可选的复查：`sudo apt install libimage-exiftool-perl`。

### 已经在用 uv？

```sh
uv tool install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

然后重新打开终端，运行 `magicdispel --check` 检查安装。

## 使用

```sh
magicdispel --check
magicdispel --version
magicdispel "照片 1.jpg" "照片 2.heic"
magicdispel --anonymous "照片 1.jpg"
```

文件名有空格时用引号包住，或者直接把文件拖进终端。Windows 是否支持拖入取决于所用的终端，
输入带引号的路径始终可用。不支持直接传入文件夹。
界面语言跟随系统（简体中文或英文），也可以设置 `MAGICDISPEL_LANG=zh` 或 `en` 指定。
ExifTool 装在不常见的位置时，可以把 `MAGICDISPEL_EXIFTOOL` 设为它的完整路径。

退出码：`0` 成功或显示帮助，`1` 有文件未能清理，`2` 参数有误，`130` 被中断。
批量处理时，某个文件失败不会影响其余文件。

## 支持的格式

| 格式 | 保留 | 清除 |
| --- | --- | --- |
| JPEG | 图像数据、JFIF 密度、方向、DPI、色彩空间、ICC 配置；HDR 增益图（MPF）、Apple HDR 余量和增益图 XMP | 其余 EXIF 和 XMP、IPTC/Photoshop、注释、C2PA、缩略图、厂商私有数据、末尾附加数据 |
| PNG、APNG | 图像数据、调色板、透明度、色彩声明（sRGB、gAMA、cHRM、cICP、HDR）、DPI、动画、ICC 配置、方向 | 文字、时间戳、C2PA、私有数据块、结尾之后的数据 |
| HEIC、HEIF | 图像条目和图块、HDR 增益图（Apple 和 ISO）、透明度、方向、ICC 配置、HDR XMP 字段 | EXIF、其余 XMP、Apple 私有属性表、深度与镜头校准、人像与语义蒙版、风格编辑图、缩略图、未使用的数据 |
| AVIF | 同 HEIF，并支持动画；动画的时间戳、名称和用户数据会被清空 | 同 HEIF |
| WebP | 图像数据、透明度、动画、ICC 配置、方向 | 其余 EXIF、XMP、未知数据块 |
| GIF | 图像、调色板、帧时序、透明度、循环次数、ICC 配置 | 注释、文字叠加层、XMP、其他扩展 |
| TIFF | 图像数据、解码所需标签、DPI、方向、页码、ICC 配置 | EXIF 和 GPS 目录、XMP、IPTC、Photoshop 数据、描述文字、私有标签、子图 |
| BMP | 无损转为 PNG，像素、DPI 和色彩配置不变 | 其余全部 |
| RAW、视频、PDF | 不支持；基于 TIFF 的 RAW（DNG、CR2、NEF 等）会被识别并拒绝 | |

无法安全重建的特殊变体会被拒绝，而不是原样放行，例如 BigTIFF、分片的图像序列、未知类型的 HEIF 条目。
最大可处理 2.68 亿像素的图片（足够容纳 2 亿像素手机拍的照片），更大的会被拒绝。

## 开发

见[英文说明](README.md#development)，其中介绍了单元测试和使用真实照片样本库的回归检查。
单元测试只依赖 Pillow；装有 ExifTool 时，还会用它按其他软件的方式写入元数据，并复查每个结果。
`.github/workflows/test.yml` 已为 macOS、Windows 和 Linux 准备好，但尚未运行；
发布前必须完成 Windows 和 Linux 的验证，本机 macOS 的结果不能代表其他系统。

开源协议：MIT。可选配套工具 [ExifTool](https://exiftool.org/) 由 Phil Harvey 开发，
单独发布并遵循其自身协议。MagicDispel 与 ExifTool 没有隶属关系。
