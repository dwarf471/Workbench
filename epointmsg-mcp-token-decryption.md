# EpointMsg MCP 已有令牌解密与连接验证

记录日期：2026-09-16。用途：为后续编写 skill 留存已验证的实现素材；本文本身不是 skill。

## 已验证环境

- 客户端：新点即时通讯（EpointMsg），Electron 应用。
- 安装目录：`E:\Program Files (x86)\EpointMsg`。
- 用户配置：`C:\Users\licheng\AppData\Roaming\EpointMsg\setting_rong.json`。
- MCP 地址：`http://127.0.0.1:9527/mcp`，Streamable HTTP。
- 服务标识：`rongyun-im-mcp`，版本 `1.0.0`。
- 握手协议版本：`2025-11-25`。

配置路径由客户端 `src/configuration.js` 使用 `app.getPath('userData')` 拼接 `setting_rong.json` 得到，不在安装目录。其他机器的用户名、用户数据目录和端口可能不同，应先核实。

## 为什么需要解密

配置中的 `mcpToken` 是已有 MCP 令牌。当前客户端将它加密保存，不能把配置中的密文直接作为 Bearer Token。

客户端源码位置：安装目录下 `resources\app.asar` 中的：

- `src/modules/mcp/crypto.js`：加解密算法。
- `src/modules/mcp/index.js`：读取、生成及校验令牌。
- `src/configuration.js`：配置文件路径。

设置入口受 `epointJson.enableMCPEnter` 控制；入口隐藏不代表 MCP 服务未运行。读取已有令牌不需要修改界面、安装文件或配置，也不需要重置令牌。

## 客户端算法

| 项目 | 实现 |
| --- | --- |
| 算法 | AES-256-CBC |
| 密钥 | 对 UTF-8 字符串 `msgmcp` 做 SHA-256，取完整 32 字节摘要 |
| IV | 密文封装第二段，Base64 解码得到 16 字节 |
| 密文 | 密文封装第三段，Base64 解码 |
| 填充 | PKCS7，与 Node.js crypto 默认填充兼容 |
| 明文编码 | UTF-8 |
| 封装格式 | `v1:<base64(iv)>:<base64(ciphertext)>` |

客户端新生成的明文令牌为 `crypto.randomBytes(32).toString('hex')`，即 64 位十六进制字符串。历史版本可能保存明文令牌；客户端对此兼容，但不能仅凭长度判断令牌有效性，最终应通过握手验证。

## PowerShell 参考脚本

此脚本只读取配置，将令牌解密到当前进程内存，并请求握手和工具列表。不读取会话或聊天内容，不写入令牌文件，不修改配置，不输出明文令牌。应仅用于用户本人明确授权的本地客户端。

```powershell
$settingsPath = 'C:\Users\licheng\AppData\Roaming\EpointMsg\setting_rong.json'
$mcpUrl = 'http://127.0.0.1:9527/mcp'
$plainToken = $null
$mcpHeaders = @{}
$plainBytes = $null

try {
    $settings = Get-Content -LiteralPath $settingsPath -Raw -ErrorAction Stop |
        ConvertFrom-Json -ErrorAction Stop
    $storedToken = [string]$settings.mcpToken
    if ([string]::IsNullOrWhiteSpace($storedToken)) {
        throw 'MCP token is missing.'
    }

    if ($storedToken.StartsWith('v1:')) {
        $tokenParts = $storedToken.Split(':')
        if ($tokenParts.Length -ne 3) {
            throw 'Invalid encrypted token envelope.'
        }

        $hashEngine = [Security.Cryptography.SHA256]::Create()
        $aesEngine = [Security.Cryptography.Aes]::Create()
        try {
            $aesEngine.Key = $hashEngine.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes('msgmcp')
            )
            $aesEngine.IV = [Convert]::FromBase64String($tokenParts[1])
            $aesEngine.Mode = [Security.Cryptography.CipherMode]::CBC
            $aesEngine.Padding = [Security.Cryptography.PaddingMode]::PKCS7
            $encryptedBytes = [Convert]::FromBase64String($tokenParts[2])
            $decryptEngine = $aesEngine.CreateDecryptor()
            try {
                $plainBytes = $decryptEngine.TransformFinalBlock(
                    $encryptedBytes, 0, $encryptedBytes.Length
                )
                $plainToken = [Text.Encoding]::UTF8.GetString($plainBytes)
            }
            finally {
                $decryptEngine.Dispose()
            }
        }
        finally {
            $aesEngine.Dispose()
            $hashEngine.Dispose()
        }
    }
    elseif ($storedToken -match '^[0-9a-fA-F]+$') {
        # Compatibility with historical plaintext storage.
        $plainToken = $storedToken
    }
    else {
        throw 'Unsupported token format; recheck client algorithm.'
    }

    $mcpHeaders = @{
        Authorization = 'Bearer ' + $plainToken
        Accept = 'application/json'
    }
    $checks = @(
        @{
            jsonrpc = '2.0'
            id = 1
            method = 'initialize'
            params = @{
                protocolVersion = '2025-11-25'
                capabilities = @{}
                clientInfo = @{ name = 'local-mcp-check'; version = '1.0' }
            }
        },
        @{ jsonrpc = '2.0'; id = 2; method = 'tools/list' }
    )

    foreach ($check in $checks) {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $mcpUrl `
            -Method Post -Headers $mcpHeaders -ContentType 'application/json' `
            -Body ($check | ConvertTo-Json -Depth 8 -Compress) `
            -TimeoutSec 10 -ErrorAction Stop
        $result = $response.Content | ConvertFrom-Json -ErrorAction Stop
        if ($result.error) {
            throw "JSON-RPC error code: $($result.error.code)"
        }
        Write-Output "$($check.method): HTTP $($response.StatusCode)"
        if ($check.method -eq 'initialize') {
            Write-Output "Server: $($result.result.serverInfo.name)"
            Write-Output "Protocol: $($result.result.protocolVersion)"
        }
        else {
            foreach ($tool in $result.result.tools) {
                Write-Output "Tool: $($tool.name)"
            }
        }
    }
}
catch {
    # Avoid printing request headers, raw settings, or credential-bearing objects.
    if ($_.Exception.Response) {
        Write-Output "MCP request failed: HTTP $([int]$_.Exception.Response.StatusCode)"
    }
    else {
        Write-Output 'Local decoding or connection verification failed; check path, format, and service.'
    }
    throw 'MCP verification did not complete.'
}
finally {
    if ($plainBytes) {
        [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    }
    $plainToken = $null
    $mcpHeaders.Clear()
    $storedToken = $null
    $settings = $null
}
```

注意：清空变量与字节数组只是尽量缩短凭证驻留时间，不能保证清除 .NET 不可变字符串、临时副本或进程内存中的所有残留。这里的“内存中解密”表示不主动将明文持久化到文件或日志，不代表绝对的内存安全保障。

## 本次验证结果

已使用现有配置中的令牌，按上述客户端算法解密后完成验证：

- 解密后的令牌长度：64；未输出真实值。
- `initialize`：HTTP 200，返回服务标识 `rongyun-im-mcp`。
- `tools/list`：HTTP 200。
- 未调用聊天记录或会话查询工具。
- 未重置令牌，未修改配置或安装文件。

客户端工具定义包含：`get_conversation_list`、`get_history_messages`、`search_contacts`、`get_profile`。后续查询聊天应另外获得用户明确的查询范围，不在连接验证阶段默认读取。

## 后续 skill 的边界与排错素材

- 先确认 EpointMsg 进程和端口归属，再访问 MCP；不要把任意本地端口当作新点服务。
- 从实际用户数据目录读取配置，不遍历无关凭证文件。
- 若客户端升级、封装版本变化或解密失败，重新核对客户端源码，禁止自动生成或重置令牌。
- HTTP 401：已有令牌可能与当前进程不一致、已经重置，或使用了错误服务地址；重新只读检查，不尝试绕过鉴权。
- 连接失败：检查客户端是否运行、端口是否监听、实际端口是否改变。
- HTTP 200 不一定表示成功，仍需检查 JSON-RPC `error` 与预期 `result`。
- 当前服务实现是无状态设计，握手不下发 session，验证脚本无需保存 `MCP-Session-Id`。若未来实现变化，应按新协议调整。
- 不打印令牌、Authorization 请求头、完整配置或包含凭证的异常对象；不要将真实令牌写入 Markdown、skill 示例或版本库。
- 固定种子派生密钥仅提供落盘混淆式保护，不能阻止有权限读取配置和程序代码的本机进程解密。

本文未保存任何真实令牌或用户聊天内容。
