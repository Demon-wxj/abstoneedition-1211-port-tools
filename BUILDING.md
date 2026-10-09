# 迁移与检查

准备 Python 3，将合法取得的原 Mod JAR 和 Minecraft 1.21.1 客户端 JAR 放入 `inputs/`，然后在仓库根目录运行：

```powershell
python tools/port_resources.py migrate --jar inputs/abstoneedition-2.1.3-fabric-mc1.20.jar --resources output/resources --report output/resource-port-report.json
python tools/port_resources.py check --resources output/resources --report output/resource-port-report.json --vanilla-jar inputs/minecraft-client.jar
```

迁移资源写入 `output/resources/`，文件清单写入 `output/resource-port-report.json`，原 JAR 不变。`check` 检查数据格式、资源引用和清单哈希；缺少 Minecraft 客户端 JAR 时，原版模型和纹理引用不会得到完整检查。

这里没有上游 Java 源码或构建工程，不能用这些命令编译完整 Mod。上游资源仍是 ARR，迁移不会授予额外的再分发权限。
