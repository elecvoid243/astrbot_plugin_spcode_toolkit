# file-write 保存格式保持设计

> Author: elecvoid243
> 时间戳: 2026-07-24 16:34 CST
> 状态: 已确认

## 1. 背景

`POST /spcode/file-write` 当前把请求中的 `content` 固定编码为 UTF-8，并通过
`Path.write_text(..., encoding="utf-8", newline="")` 写盘。另一方面，
`GET /spcode/file-browser` 会把读取到的 `CRLF` 和 `CR` 统一转换为 `LF` 后返回给前端。
因此，用户通过文件浏览器读取、编辑并保存已有文件时会发生两类非预期变化：

1. GBK、CP936、GB18030、Latin-1 或带 UTF-8 BOM 的文件被改写为无 BOM UTF-8；
2. CRLF 文件被改写为 LF。

这会扩大 Git diff，并可能影响依赖既有字符编码、BOM 或换行约定的 Visual Studio
项目及外部构建工具。

## 2. 目标

- 已有文本文件保存后保持原字符编码。
- 已有 UTF-8 BOM 文件保存后继续保留 BOM。
- 已有文本文件保存后保持其主导换行格式。
- 新建文件继续使用 UTF-8（无 BOM）和 LF。
- 不修改现有请求体协议：`{path, content, umo?, worktree?}`。
- 保持既有路径安全、2 MB 请求内容限制、upsert 和结构化响应行为。

## 3. 非目标

- 不新增前端必传字段。
- 不改变 `file-browser` 当前返回 LF 规范化文本的行为。
- 不把 `/spcode/file-write` 扩展为二进制文件编辑端点。
- 不支持当前 `file-browser` 无法作为文本预览的 UTF-16/UTF-32 文件。
- 不保证逐行保持混合换行；混合换行统一为主导格式。

## 4. 方案选择

### 4.1 备选方案

1. **服务端自动保持（采用）**：覆盖已有文件前读取原始字节，检测编码、BOM 和换行，
   再按检测结果编码写回。无需修改前端协议，兼容现有调用方。
2. **前端回传元数据**：由 `file-browser` 返回并由前端回传 `encoding`、`newline`。
   数据流显式，但需要同步升级前端，并存在客户端伪造或过期元数据问题。
3. **继续统一 UTF-8 + LF**：实现最简单，但不能满足保存前后格式一致的需求。

采用方案 1。

## 5. 数据流

### 5.1 已有文件

1. 按现有流程校验请求体、路径与 Git 工作区。
2. 使用二进制方式读取目标文件的原始字节。
3. 复用 `file-browser` 的文本解码策略检测编码：
   - UTF-8 BOM -> `utf-8-sig`；
   - UTF-8；
   - CP936；
   - GBK；
   - GB18030；
   - Latin-1 兜底。
4. 在解码后的原始文本中统计 `CRLF`、独立 `LF` 和独立 `CR`。
5. 选择出现次数最多的换行格式；数量相同且存在 CRLF 时优先 CRLF；无换行时
   使用 LF。
6. 将请求内容中的 `CRLF`、`CR` 先统一为 LF，再转换为选定换行格式。
7. 在内存中按原编码完成编码；只有编码成功后才覆盖目标文件。

### 5.2 新建文件

- 编码：UTF-8，无 BOM；
- 换行：LF；
- 继续自动创建缺失的父目录。

## 6. 错误处理

- 原文件读取失败：记录异常日志并返回 `git_error`，不覆盖目标文件。
- 新内容无法由原编码表示：返回 `invalid_param` 并附带简短错误信息，不覆盖目标文件。
- 文件写入失败：记录异常日志并返回 `git_error`。
- 已存在但不是常规文件：继续返回 `file_not_found`。
- 路径安全失败：继续返回 `path_unsafe`。

## 7. 实现范围

### 修改

- `tools/webapi/file_write.py`
  - 增加文件格式描述数据结构；
  - 增加已有文件编码/换行探测；
  - 增加换行规范化与按原编码生成字节；
  - handler 改为编码成功后按字节写入。
- `tests/test_file_write.py`
  - 增加编码、BOM 和换行保持回归测试。

### 不修改

- `/spcode/file-write` 路由与请求体字段；
- `tools/webapi/file_browser.py` 的响应结构；
- `_conf_schema.json`；
- 其他文件写入端点。

## 8. 测试策略

必须先在旧实现上观察新增测试失败，再修改生产代码：

1. UTF-8 + CRLF 文件保存后仍为 UTF-8 + CRLF；
2. UTF-8 BOM 文件保存后仍保留 BOM；
3. GBK/CP936 + CRLF 文件保存后仍能按原编码解码且保持 CRLF；
4. LF 文件保存后仍为 LF；
5. 混合换行按主导格式统一；
6. CRLF/LF 数量相同且含 CRLF 时选择 CRLF；
7. 新建文件使用 UTF-8 无 BOM + LF；
8. 原编码无法表示新字符时返回错误且原始字节不变。

验证命令：

```text
D:\anaconda3\envs\astrbot\python.exe -m pytest tests/test_file_write.py -v
D:\anaconda3\envs\astrbot\python.exe -m pytest tests/ -q
```

Python 文件 lint 使用项目内置 `code_check` 工具执行 ruff。

## 9. 兼容性

- 请求协议保持不变，现有 Dashboard 无需升级。
- 新建文件行为保持 UTF-8 无 BOM + LF，与当前实现一致。
- 已有 UTF-8 + LF 文件行为保持不变。
- 仅修复已有非 UTF-8、UTF-8 BOM、CRLF 和 CR 文件被无条件规范化的问题。
