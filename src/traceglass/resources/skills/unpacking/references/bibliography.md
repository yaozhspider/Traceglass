# 本地摘录目录

分析时读 `sources/` 里的文件。不要为了「看看链接」去联网。标题只用于报告里注明出处。

| 要查的问题 | 读这个文件 |
|------------|------------|
| 壳分 Type I–VI，一次 dump 够不够 | [sources/sok-packer-types.md](sources/sok-packer-types.md) |
| 怎么找 OEP、为什么要修 IAT、HollowsHunter/Scylla 怎么用 | [sources/pe-oep-dump-iat.md](sources/pe-oep-dump-iat.md) |
| Pin、Unicorn、QEMU、x64Unpack、Xunpack 各做到哪 | [sources/emulation-dbi.md](sources/emulation-dbi.md) |
| APK 加固、DEX 为什么 dump 不全、.NET cctor、PyInstaller | [sources/android-dotnet-bundles.md](sources/android-dotnet-bundles.md) |
| Unpac.me / CAPE / Demida / vmpunpack 的限制 | [sources/services-and-protectors.md](sources/services-and-protectors.md) |
| NoVmp 的适用版本、VMEnter RVA、VTIL 与实验性重编译 | [sources/novmp.md](sources/novmp.md) |

摘录覆盖的材料：

- Ugarte-Pedrero 等，SoK: Deep Packer Inspection，IEEE S&P 2015
- hasherezade，TinyTracer + PE-sieve 脱壳教程，2025；PE-sieve / HollowsHunter / MalUnpack 的分工说明
- Scylla 的 IAT Autosearch → Get Imports → Dump → Fix Dump
- Pinicorn（2024）、GUARD（SAC 2025）、Unipacker、x64Unpack、Xunpack（RAID 2023）
- *To Unpack or Not to Unpack*，arXiv:2509.16340；frida-dexdump 与运行时 ClassLoader 观察
- ConfuserEx2 的「cctor 之后 dump，再 de4dot / 字符串 / ProxyCall」；pyinstxtractor 的版本与 pyc 头
- Unpac.me 解包说明（2023-12）；CAPE 的 OEP dump 与改 UPX package；Demida、ThemidaUnpacker、vmpunpack 的自述限制
- can1357/NoVmp README 与本地安装验证，源码版本 `6c23c9a335f70e8d5ed6299668fd802f2314c896`（2021-05-05）

书和长文没有逐页抄入。需要时用上面本地摘录里的结论，不要假设模型还能打开网页。
