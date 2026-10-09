# 加密实验手册

本手册用于 Runtime Sandbox 中的授权样本，不能把关键词命中当作最终结论。

## 候选识别

```sh
case_dir=/workspace/case-<id>
mkdir -p "$case_dir/snapshots/before" "$case_dir/experiments"
cp --reflink=auto /samples/<sample> /workspace/case-<id>/sample
sha256sum /workspace/case-<id>/sample > /workspace/case-<id>/journal.txt
strings -a -n 5 /workspace/case-<id>/sample > /workspace/case-<id>/strings.txt
crypto-detector --methods=keyword --output /workspace/case-<id>/detector /workspace/case-<id>/strings.txt
```

`crypto-detector` 是源码/文本启发式扫描器，`.crypto` 文件只属于候选证据，必须结合反汇编、常量和数据流确认。需要 API 扫描时单独使用 `--methods=api`。

## 有界动态实验

```sh
cp --reflink=auto "$case_dir/sample" "$case_dir/snapshots/before/sample"
sha256sum "$case_dir/sample" > "$case_dir/snapshots/before/sample.sha256"
timeout 30s strace -f -o /workspace/case-<id>/strace.log /workspace/case-<id>/sample <args>
```

调试实验应记录全部命令、断点和输出。实验失败后恢复 `snapshots/before`，撤销 patch/hook，再创建新的实验目录；不得联系网络指标或在分析过程中安装软件包。

## 复现代码与证据记录

为每个假设创建独立目录，例如：

```text
/workspace/case-<id>/experiments/exp-001/
├── hypothesis.md
├── recover.py（或其他源码）
├── input.bin
├── output.bin（如有）
├── command.txt
├── stdout.txt
├── stderr.txt
├── hashes.txt
└── result.json
```

可选择 Python 脚本，也可根据目标语义和沙箱工具链编译 C、Go 或 Rust。
脚本必须复现已提出假设中的字节序、溢出、轮数、旋转、分组模式、padding、
key/IV 派生和输出长度。每次实验只改变一个变量，脚本和样本均通过 sandbox
MCP 执行。

实验完成后调用 `record_crypto_experiment`，例如（`artifact_dir` 必须是当前
Runtime Sandbox 的 `/workspace/` 下的实验子目录或其相对路径；不要传宿主机路径）：

```text
experiment_id="exp-001"
hypothesis="候选 AES-CBC 解密，待确认 key/iv"
algorithm="AES-CBC (candidate)"
artifact_dir="/workspace/case-<id>/experiments/exp-001"
command="timeout 30s python3 recover.py"
execution_status="completed"
validation_json='{"algorithm":{"state":"candidate"},"implementation":{"state":"validated","basis":"standard vector"},"recovery":{"state":"supported","basis":"two sample inputs"}}'
evidence_summary="实验 exp-001 的脚本输出与样本输出在指定输入上的比较结果"
```

该调用会把实验详情写入 `experiments.jsonl`，并在 `evidence.jsonl` 生成关联
证据；Web 可用 `record_evidence` 把关键结论关联到实际归档文件。未经过独立
验证时不能填写 `validated`；失败实验保留工件并标记为 `failed` 或 `rejected`。
