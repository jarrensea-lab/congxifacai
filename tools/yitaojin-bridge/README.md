# 易淘金桥接安全测试

桥接源码或 `SelfTests` 变化后，通过 SwiftPM 运行安全门：

```zsh
swift package --package-path tools/yitaojin-bridge clean
swift test --package-path tools/yitaojin-bridge
```

正式、无测试框架依赖的 test target 通过确定性的 build-tool gate 调用唯一的
49 项 `SelfTests`。所有声明输入均未变化时，SwiftPM 可能复用上次成功结果。
需要无条件重新执行全部 49 项检查时，运行：

```zsh
zsh scripts/test-yitaojin-bridge.sh
```

安全检查只运行 fake-client harness；不会启动广发易淘金、读取真实账户或写入自选股页面。
