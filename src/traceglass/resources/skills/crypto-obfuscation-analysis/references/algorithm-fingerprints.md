# 常见算法指纹

这些是候选线索，不是单独结论。必须结合调用可达性、数据流和实验验证。

| 候选 | 结构线索 |
|---|---|
| AES | S-box/Rcon、16 字节块、10/12/14 轮或标准库调用 |
| TEA/XTEA | `0x9e3779b9`、两个 32 位 word、约 32 轮、加法/XOR/移位组合 |
| XXTEA | 可变长度 word 数组、MX 函数、`q=6+52/n`、delta 累加 |
| 3DES | DES 置换/S-box 重复三次；EDE 顺序，2-key 时 K3=K1 |
| SM4 | FK/CK 常量、32 轮、16 字节块、128 位 key |
| RC4 | 256 字节 S 盒、KSA 交换循环、PRGA 输出循环 |
| ChaCha20 | `expand 32-byte k`、四 word quarter-round、20/12/8 轮 |
| SHA-1/SHA-256 | 固定初始状态、消息扩展、80/64 轮 |
| MD5 | 64 轮、四组状态和 T 常量 |
| CRC | 查表或逐位移位、固定多项式、短固定输出 |
| Base64/Hex | 受限字符集、长度规律、查表编码，通常无 key |
| ROT13/ROT47 | 固定字符轮转，分别作用于字母或可打印 ASCII，无 key |
| LZ/ZIP | 格式头、滑动窗口、压缩库调用和可逆长度变化 |

## 变体归一化

先记录标准骨架，再记录差异字段：

```yaml
family: AES
variant: custom
mode: CBC
differences:
  - custom_sbox
  - shift_rows_offsets: [0, 2, 3, 1]
  - post_round_xor: 0xa5
```

这样可以区分“标准实现被混淆”和“算法本身被修改”。

对于自定义变换，记录每个操作的位宽、溢出、端序、轮数和状态更新顺序。
