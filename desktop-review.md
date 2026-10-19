# 个人工作台桌面版 0.2.0

## 范围与结构

目标是 Windows 个人单窗口应用：双击启动、独立窗口、不要求使用者安装开发环境。Vue 页面与原有 FastAPI 业务保持复用；pywebview 在主线程管理窗口，Uvicorn 在后台线程运行，数据库 worker 随应用退出。暂不提供托盘常驻、自动更新、自动同步或远程访问。

- `backend/desktop.py`：入口、首次导入、单实例、退出提示、原生保存及桌面冒烟测试。
- `backend/app/paths.py`：只读程序资源与可写数据目录。
- `backend/app/desktop_runtime.py`：随机回环端口、服务生命周期、实例唤醒。
- `backend/app/desktop_http.py`：会话认证、同源与 Host 检查。
- `backend/app/desktop_storage.py`：旧工作台一致快照导入，不覆盖已有数据。
- `backend/app/credentials.py`：保留 Windows DPAPI 密文兼容性；Mac 使用 Keychain 保存本地加密密钥，尚未实机验收。
- `frontend/src/desktop.ts`：原生文件保存；普通浏览器保持原下载行为。
- `packaging/workbench.spec`、`packaging/workbench.iss`：目录式程序包、当前用户安装器。

会话凭据仅用于本机进程间唤醒及 HTTP 认证，随机生成，退出后删除 `.desktop-session.json`；不复用模型或 MCP 凭据。该文件位于当前用户数据目录，不构成同一 Windows 用户下恶意进程的安全隔离。

## 开发与打包

在项目根目录执行：

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-build.txt
```

安装依赖只在开发或构建时进行。普通桌面启动不安装依赖、不构建前端。源码启动可运行 `start-desktop.cmd`，或用独立验收数据目录：

```powershell
backend/.venv/Scripts/python.exe backend/desktop.py --data-dir "D:/Workbench-Test"
```

制作目录式程序包：

```powershell
powershell -ExecutionPolicy Bypass -File build-desktop.ps1
```

制作安装包需要官方 Inno Setup 6.7.3 或兼容版本。构建脚本会下载并验证微软签名的 WebView2 引导程序：

```powershell
powershell -ExecutionPolicy Bypass -File build-desktop.ps1 -InnoCompiler "C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
```

本次工作环境的编译器位于 `.build-tools/inno/ISCC.exe`。构建工具、安装包、数据及依赖不提交 Git。官方来源：[Inno Setup](https://jrsoftware.org/isdl.php)、[WebView2 分发](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。

产物：`dist/PersonalWorkbench/PersonalWorkbench.exe` 及 `dist/PersonalWorkbench-0.2.0-Setup.exe`。安装器为当前用户安装，不要求管理员权限；WebView2 缺失时需要网络。应用安装包未进行代码签名。

## 验证方法

```powershell
cd backend
.venv/Scripts/python.exe -m pytest -q
cd ../frontend
npm.cmd run build
npx.cmd playwright test tests/sync.spec.ts tests/archive.spec.ts tests/extraction.spec.ts tests/desktop.spec.ts
```

上述浏览器测试要求已有服务监听 `127.0.0.1:8765`，业务请求使用测试响应；不要顺带运行 `smoke.spec.ts`，它会保存真实连接配置并检查 MCP。

桌面冒烟测试必须显式指定独立数据目录。下面在项目根目录运行，创建目录后可验证真实 WebView2 页面、数据库就绪、原生接口和重复启动，随后自动退出：

```powershell
New-Item -ItemType Directory -Force build | Out-Null
backend/.venv/Scripts/python.exe backend/desktop.py --data-dir "$PWD/build/desktop-test" --smoke-test "$PWD/build/desktop-smoke.json"
```

安装包中的 EXE 也支持相同测试参数；应使用另一个全新数据目录。测试结果的 `ok` 为真且 `second_launch_exit` 为 0 才算通过。默认用户数据目录不得用于自动测试。

手动验收：

1. 安装后双击桌面快捷方式，选择空白工作台或停止旧服务后导入旧目录。
2. 核对会话、消息、需求记录及模型提示词；模型密钥不可解密时重新填写。
3. 检查连接、同步一个小范围；关闭运行中的任务时分别检查取消退出和确认退出。
4. 导出 JSON、CSV、数据库备份与需求清单，检查中文路径、取消保存、目标占用和磁盘写入失败。
5. 重复双击快捷方式，应回到已有窗口；退出后不应残留该客户端的监听端口。
6. 升级安装，确认记录仍在；卸载后用户数据目录仍保留。

## 数据与回退

桌面版与源码浏览器模式默认使用两个独立数据目录，不会互相同步。开发测试只使用临时数据；导入后的数据也是独立副本。请勿同时用两个进程打开同一个数据目录。

升级前使用“备份数据库”，并另行保留来源配置、模型配置、姓名 CSV 和需求清单；数据库备份不含这些文件。当前桌面改造未增加数据库迁移，仍为 `0004`。回退到浏览器版可使用原项目及其原数据；桌面新增记录不会自动合并回原库。需要转回数据时，先退出两种模式，备份双方数据，再用桌面导出的完整数据库替换目标库；不能在运行中复制 SQLite 主文件而忽略 WAL。

Mac 移植还需在目标机器构建 `.app`，验证 WKWebView、原生对话框、Keychain，以及新点 MCP 的接口与配置位置。本次 Windows 验证不能替代这些检查。

## 本次验证记录（2026-10-19）

- 后端完整测试：119 项通过，覆盖原有业务及新增会话认证、动态端口、实例唤醒、WAL 快照导入、导入失败、原生保存取消与失败保护。
- 前端 TypeScript 校验及生产构建通过；相关浏览器测试 11 项通过（同步 4、归档 2、提取 2、桌面保存 3）。保留既有前端大包警告与测试依赖弃用警告。
- 源码和打包 EXE 均在本机真实 WebView2 环境启动；安装包中的 EXE 在独立目录安装后再次验证，界面与 SQLite 就绪、桌面接口可用，重复启动进程正常退出。
- 在隔离安装目录执行安装、同版本覆盖安装及卸载；独立测试数据库中的标记记录和完整性检查均保持通过。退出后会话文件已删除、监听端口已关闭。
- 本机证据：`build/installer-acceptance-20261019-164249/result.json`、`smoke.json`，构建日志在 `build/desktop-build.log`、`build/installer-build.log`。证据与安装包不上传 Git。
- 自动检查未调用真实 MCP 同步或外部模型，未使用个人归档库；没有在缺少 WebView2 的干净 Windows 机器上验证引导安装，也没有进行 Mac 实机验证。原生对话框的手动操作体验及真实业务试用仍待用户验收。
