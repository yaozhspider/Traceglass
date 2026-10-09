# 当前工具清单

核对日期：2026-10-09。REA 来自实际 MCP 工具发现记录，reverse 来自当前注册代码。工具已注册不代表每个后端能力在当前平台均可执行。

## AgentScope 原生与项目规划工具

`Bash`、`Read`、`Write`、`Edit`、`Glob`、`Grep`、`TaskCreate`、`TaskList`、`TaskGet`、`TaskUpdate`、`TodoWrite`、`AnalysisReview`、`PrepareAnalysisInput`、`ToolStop`、`TeamCreate`、`AgentCreate`、`TeamSay`、`TeamDelete`、`reset_tools`、`Skill`。

主 Agent 获取规划和团队管理能力；子 Agent 使用 TeamSay 汇报。AgentInvite 仅在有可邀请成员时出现。调度工具由原生 schedule_tools 分组按需启用。

## 原项目 reverse MCP：41 个

完整调用名以 `mcp__reverse__` 开头。

- `hash_file`
- `identify_file`
- `extract_strings`
- `binary_metadata`
- `list_sections`
- `list_imports`
- `list_exports`
- `list_functions`
- `rank_key_functions`
- `disassemble_function`
- `decompile_function`
- `detect_packer`
- `yara_scan`
- `extract_ioc_candidates`
- `ghidra_summary`
- `ida_headless_call`
- `identify_dynamic_report`
- `dynamic_report_overview`
- `dynamic_process_tree`
- `query_dynamic_events`
- `extract_dynamic_iocs`
- `rank_dynamic_behaviors`
- `correlate_static_dynamic_evidence`
- `malware_tool_capabilities`
- `floss_strings`
- `capa_analyze`
- `pe_anomaly_scan`
- `pe_header_report`
- `validate_sigma_rule`
- `convert_sigma_rule`
- `crypto_detector_scan`
- `reverse_tool_capabilities`
- `reverse_tool_call`
- `read_skill_resource`
- `analysis_case_info`
- `read_command_output`
- `save_analysis_artifact`
- `record_evidence`
- `validate_reverse_report`
- `archive_sandbox_experiment`
- `record_crypto_experiment`

## REA MCP：138 个（6.1.0）

完整调用名以 `mcp__rea__` 开头。12 个常用工具默认启用，其余用原生 reset_tools 按需启用对应 rea_* 分组。

- `annotate_native_function`
- `inspect_native_load_image`
- `inspect_native_data_type`
- `inspect_native_instruction`
- `resolve_native_call_targets`
- `address_name`
- `comment`
- `current_address`
- `current_procedure`
- `current_document`
- `goto_address`
- `inline_comment`
- `list_bookmarks`
- `list_documents`
- `list_names`
- `list_procedures`
- `list_segments`
- `list_strings`
- `next_address`
- `prev_address`
- `procedure_address`
- `procedure_assembly`
- `procedure_callees`（默认启用）
- `procedure_callers`（默认启用）
- `procedure_info`（默认启用）
- `read_function_instructions`
- `read_bytes`（默认启用）
- `address_to_file_offset`
- `procedure_references`
- `procedure_pseudo_code`（默认启用）
- `resolve_containing_procedure`
- `search_procedures`（默认启用）
- `search_strings`（默认启用）
- `set_address_name`
- `set_addresses_names`
- `set_bookmark`
- `set_comment`
- `set_inline_comment`
- `unset_bookmark`
- `xrefs`（默认启用）
- `trace_native_values`
- `inspect_native_dispatch_metadata`
- `get_objc_classes`
- `get_objc_protocols`
- `batch_decompile`
- `get_call_graph`
- `analyze_swift_types`
- `find_xrefs_to_name`
- `binary_overview`（默认启用）
- `analyze_function`
- `inspect_native_api`
- `trace_feature`
- `trace_call_path`
- `trace_native_ui_action`
- `observe_native_ui`
- `capture_native_ui_scenario`
- `inspect_macho`
- `inspect_signature`
- `inspect_plist`
- `list_architectures`
- `demangle_swift`
- `inspect_artifact`
- `extract_artifact`
- `decode_interface_builder`
- `inspect_keyed_archive`
- `inspect_asset_catalog`
- `inspect_managed_artifact`
- `inspect_managed_members`
- `inspect_managed_native_boundaries`
- `compare_managed_members`
- `verify_managed_native_boundaries`
- `import_managed_reconstruction`
- `project_managed_application_graph`
- `inspect_android_package`
- `search_android_classes`
- `inspect_android_class`
- `inspect_android_method`
- `trace_android_references`
- `inspect_firmware_regions`
- `extract_firmware`
- `export_web_scripts`
- `list_browser_targets`
- `inspect_web_page`
- `analyze_web_bundle`
- `observe_web_session`
- `discover_webmcp_tools`
- `compare_web_captures`
- `capture_web_screenshot`
- `compare_web_screenshots`
- `capture_browser_scenario`
- `list_electron_targets`
- `inspect_electron_page`
- `analyze_javascript_application`
- `reconcile_javascript_runtime`
- `capture_electron_scenario`
- `list_javascript_runtime_targets`
- `observe_javascript_runtime`
- `trace_application_feature`
- `trace_javascript_semantics`
- `compare_application_versions`
- `compare_source_to_bundle`
- `compare_javascript_export_shapes`
- `build_reconstruction_obligation_ledger`
- `evaluate_reconstruction_coverage`
- `project_android_application_graph`
- `project_apple_application_graph`
- `trace_web_module_imports`
- `trace_web_source_location`
- `observe_web_execution`
- `inspect_web_event_listeners`
- `recover_javascript_sources`
- `open_binary`（默认启用）
- `close_binary`（默认启用）
- `binary_session`（默认启用）
- `export_evidence_bundle`
- `import_evidence_bundle`
- `get_evidence_bundle`
- `capture_process_scenario`
- `compare_process_captures`
- `compare_artifacts`
- `compare_functions`
- `compare_bundles`
- `find_changed_behavior`
- `build_call_path`
- `correlate_static_and_runtime`
- `verify_reconstruction`
- `list_unknowns`
- `record_unknown`
- `update_unknown`
- `verify_unknown_resolution`
- `get_navigation_context`
- `inspect_address_context`

- `inspect_binary_layout`
- `inspect_evm_interface`
- `inspect_recorded_crash`
- `inspect_web_network_capture`
- `observe_native_calls`
- `trace_dylib_resolution`

## 工作流和共享证据适配工具

`ListReaWorkflows`、`GetReaWorkflow`、`QueryReaEvidence`、`ReadReaEvidence`。
这些是 Traceglass 的原生适配工具，不计入 138 个 REA MCP 工具。六个上游工作流为：

- `investigate_feature`
- `compare_application_versions`
- `verify_reconstruction`
- `trace_crash`
- `audit_residual_unknowns`
- `prepare_bounded_process_capture`

## IDA MCP

已配置 idapro；其工具列表由运行中的 IDA MCP 服务动态返回，本文未重新枚举，因此不计入上述数量。

## 本地模式补充工具

`inspect_file`、`extract_strings`、`write_report` 只在 local 工作区模式注入。当前部署使用 Docker，文件及报告操作通过原生工具和 reverse MCP 提供。
