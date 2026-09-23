# MagicDispel

在本机清除照片里的常见隐私元数据，保留画质。BMP 无损另存为 PNG，其他格式不重新压缩。

**尚未发布的草稿：** 以下公开安装说明需要在完成验证和发布后使用。

```sh
magicdispel 照片.jpg
```

输入 `magicdispel`，加一个空格，把一张或多张照片拖入终端，按回车。
新文件保存在原图旁，例如 `照片_clean.jpg`；原图不变，重名自动编号。
使用 `magicdispel --anonymous 照片.jpg` 可改为随机文件名，例如 `photo_<随机字符>.jpg`，
避免原名称里的姓名、日期等跟随输出。此选项只匿名化文件名，不会匿名化画面内容。

## 安装

需要 Python 3.10+ 和 ExifTool 12.73+。近期 iPhone 的 HEIC 建议使用 ExifTool 13.55+。
ExifTool 是独立依赖，不会随着 Python 包自动安装。

首版通过 GitHub 发布，无需等待 PyPI 收录。

### macOS

已安装 Homebrew 的用户执行：

```sh
brew install exiftool pipx
pipx ensurepath
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

重新打开终端，运行 `magicdispel --check` 检查安装。

### Windows

在 PowerShell 安装依赖：

```powershell
winget install --exact --id Python.Python.3.12
winget install --exact --id OliverBetz.ExifTool
```

重新打开 PowerShell，执行：

```powershell
py -3.12 -m pip install --user pipx
py -3.12 -m pipx ensurepath
py -3.12 -m pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

再次打开终端，运行 `magicdispel --check`。

### Linux

Ubuntu 24.04+ 或较新的 Debian：

```sh
sudo apt update
sudo apt install pipx libimage-exiftool-perl
pipx ensurepath
pipx install "https://github.com/v1nc3nt-continualab/magicdispel/archive/refs/tags/v0.1.0.zip"
```

重新打开终端，运行 `magicdispel --check`。发行版提供的 ExifTool 如果过旧，
请按 [ExifTool 官方安装说明](https://exiftool.org/install.html) 升级。

## 使用

```sh
magicdispel --check
magicdispel --version
magicdispel "照片 1.jpg" "照片 2.heic"
magicdispel --anonymous "照片 1.jpg"
```

支持 JPG、PNG/APNG、HEIC/HEIF、AVIF、WebP（含无损和动画）、GIF、TIFF、BMP。
BMP 输出为 PNG；GIF、动态 PNG 保留动画，TIFF 保留页数和位深。
带 MPF/HDR 附加图层的 JPEG 会逐张清理主图与附加图，保留已识别的 HDR 显示信息并重建索引。
暂不支持 RAW、视频、PDF 和直接传入文件夹。

命令后面必须有空格。文件名有空格时用引号包住；终端支持拖入文件时，也可以直接拖入。
Windows 是否支持拖入取决于使用的终端，输入带引号的文件路径始终可用。

导出前会比较保留下来的图像编码、方向和已知 HDR 参数，检查常见隐私字段。
HEIC 会先按依赖关系移除已识别的辅助预览、深度/镜头校准、蒙版和风格编辑图，
保护主图、HDR、透明度和共享图块；无法安全处理的依赖会阻止导出。
GIF、动态 PNG、AVIF（含动画）、TIFF 逐帧/逐页验证像素；TIFF 另行核对各页压缩数据，支持 JPEG 压缩 TIFF。BMP 转为 PNG 后验证像素一致。
检查失败就不导出该图片；批量处理时继续处理其他文件。

## 隐私边界

会清理常见的经纬度、拍摄时间、设备信息、作者、描述和照片标识。
保留 ICC 色彩转换数据、少量 EXIF 显示字段，以及 HEIC 的 HDR 增益图、透明度图和显示参数。

ICC 原日期统一替换为固定占位值 `2000-01-01 00:00:00`，描述统一为 `Clean`，
原有作者、设备、校准时间、配置标识，以及已识别 HDR 曲线里的图片标识会被清理。
HEIC 的 URI 私有元数据项目（包括 Apple PLIST 风格数据）会连同实际内容一起删除。
深度图、校准信息、蒙版、缩略图和风格编辑图会按图层关系清理；删除的实际字节和
不再使用的图层属性也会擦除。XMP 工具版本文字不再保留。
人像、景深和摄影风格的后期调整能力会减少；已测样例的 SDR/HDR 显示像素保持一致。

**它不是“零元数据”或“绝对匿名”工具。** 必要色彩、方向和 HDR 信息仍保留；
照片画面、默认文件名、以前公开发布过的相同照片，也可能帮助别人关联身份或地点。
此前发现的 ICC 日期、描述与 PLIST 残留已修复，旧版输出需要重新处理。
处理全程在本机完成；程序不上传图片、不收集遥测、不请求网络。

本轮实测环境为 macOS。Windows/Linux 的自动测试配置已准备好但尚未运行，
必须完成对应系统验证后再发布跨平台支持承诺；合成 HEIF 结构测试不能替代实图解码验证。

系统可能给新文件添加自己的时间戳、权限等属性，后续软件也可能再次添加信息。
更多说明见 [隐私与格式限制](docs/PRIVACY.md)。

开源协议：MIT。底层调用的 ExifTool 由 Phil Harvey 开发，单独安装并遵循其自身协议。
