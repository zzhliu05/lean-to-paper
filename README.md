# Lean → 自然语言数学文章

第一阶段建立可追溯的声明依赖图。输入是 `../lean-extracted/lean`，不改动原始源码。

## GitHub 数据快照

仓库：[zzhliu05/lean-to-paper](https://github.com/zzhliu05/lean-to-paper)。代码、统计摘要、循环候选报告和入口预览放在 Git 中；完整图数据放在 [Release `graph-2026-11-22`](https://github.com/zzhliu05/lean-to-paper/releases/tag/graph-2026-11-22)。

当前快照覆盖 **1,060,824 条声明、6,013,037 条候选引用、889,764 条模块导入记录**，候选结构层级为 **0–148**。它是词法候选图，并未通过 Lean 内核验证；Mathlib 完整依赖尚未接入。

在新克隆的仓库中下载和恢复数据：

```powershell
gh release download graph-2026-11-22 --repo zzhliu05/lean-to-paper --dir release-assets
python tools/restore_graph_release.py --assets release-assets --root .
python run.py serve --db artifacts/dependencies.sqlite --port 0
```

数据包括节点、引用边、分层、模块导入的 JSONL，以及可直接查询的 SQLite 快照。压缩包可能分片；恢复工具会按清单顺序读取分片，验证压缩和解压后的 SHA-256，并拒绝覆盖已有数据。文件清单位于 `artifacts/graph-data-manifest.json`，Release 另附 `SHA256SUMS`。只需数据库时，可为恢复命令添加 `--only dependencies.sqlite`。

原始 Lean 源文件未包含在仓库和本次 Release 中。原始输入来自 `https://alpo.ge/lean.tar.gz.part01` 至 `part14`；复算图时需自行准备 `../lean-extracted/lean`。本地未完成的自然语言审核材料也不随图快照上传。

## 已实现

- 全项目模块、声明符号目录，存储为 SQLite；文件保存 SHA-256，展开时拒绝使用已变化的源码。
- 模块导入图与声明引用图分开存储，边方向为 **使用者 → 被引用者**。
- 默认从 `Main.lean` 的五个定理展开候选引用闭包，也可指定其他根节点。
- 边记录 statement/proof、引用文本、来源文件、行号和解析依据。
- 简单命名空间、open、private 可见性、局部变量遮蔽处理；同名歧义保留在 unresolved，不任意选一个目标。
- 可续跑展开；导出完整候选闭包 JSONL，以及限深预览 JSON/Graphviz DOT。
- 单元与端到端测试覆盖词法屏蔽、范围、歧义、遮蔽、引用阶段和数据库导出。

## 图的证据等级

当前是 **源码词法候选引用图**，不是 Lean 内核证明依赖图。`candidate_closure_complete` 仅表示沿当前算法找到的候选边已展开完毕；`semantic_complete` 始终为 false。

`qualified_candidate`、`namespace_candidate`、`open_namespace_candidate` 等只是名称匹配依据。`same_module` 和 `direct_import` 标记可直接检查的可见性；`import_visibility_unchecked` 表示没有验证传递导入可见性，可能产生假阳性。方法调用/投影只能按常量前缀给出候选，不能推断接收者类型。

`phase=statement` 表示声明头，`phase=proof` 表示 `:=` 后的表达式；对 theorem/lemma 是证明，对 def/instance 等则是定义体或实例体。

省略的隐式参数、类型类实例、符号重载、notation、宏、自动生成的结构字段/构造子、局部 open 和复杂 renaming 等可能导致漏边或误边。`unresolved_external_generated_or_syntax` 也包含语法关键字和未识别的局部名称，不能全部解释成外部定理。局部变量识别是保守的，可能过度屏蔽。声明检测按行进行，不等同于完整 Lean parser。

精确版本必须用项目指定 Lean 4.33.1 和固定 Mathlib revision 编译，并从 elaborated environment 中的声明类型与证明项收集常量引用。工具链、依赖安装及全项目编译尚未进行，本项目不据词法图判断黎曼猜想等声明成立。

## 本科难度边界

`policy.json` 控制导出的文章策略标注。当前约定为**本科教材及以下不进入保留正文，只保留研究生及以上**；难度待审的条目单独列出。基础定理仍保留在依赖图中，未知难度不会导致图节点被删除。当前没有自动难度分类器，也没有把百万条声明擅自标成基础或研究级别；禁止使用本地模型筛选。自然语言全文生成和难度审核尚未完成。

可在 `overrides` 中按完整声明名人工指定 `school`、`undergraduate_textbook`、`graduate` 或 `research`。导出会标记 `article_action=omit_proof/retain`，不会删除节点或边。若希望连本科教材证明也省略，将 `proof_omission_threshold` 改为 `undergraduate_textbook_and_below`。导出命令支持 `--policy 路径`。

## 使用（PowerShell，Python 3.11+，无第三方运行依赖）

在本项目目录运行：

```powershell
python run.py catalog --source ../lean-extracted/lean --db artifacts/dependencies.sqlite
python run.py expand --db artifacts/dependencies.sqlite
python run.py export --db artifacts/dependencies.sqlite --out artifacts --depth 3
python run.py show --db artifacts/dependencies.sqlite riemannHypothesis
python run.py stats --db artifacts/dependencies.sqlite
python run.py watch --db artifacts/dependencies.sqlite --interval 3
python run.py serve --db artifacts/dependencies.sqlite --port 0
python run.py layers --db artifacts/dependencies.sqlite --out artifacts
```

`catalog` 拒绝覆盖已有数据库，需重建时使用新的文件名。`expand` 无默认节点上限；指定 `--limit 5000` 可分批工作，再运行相同命令续跑。`--root` 可以重复指定。导出目录中的 JSONL 是完整已发现候选图；`main-preview.*` 仅是指定深度视图，文件记录 frontier，不能把它当完整图。

对全部声明分层（包含不在 Main 入口闭包内的独立声明）：

```powershell
python run.py expand --db artifacts/dependencies.sqlite --all
python run.py layers --db artifacts/dependencies.sqlite --out artifacts --all
```

`expand --all` 复用已展开的数据，只补齐其余声明，并支持 `--limit` 分批续跑。`layers --all` 会检查全项目每一条声明都已展开，缺失时拒绝把它们当作无依赖叶子。全量展开不将百万条声明全部设为预览根节点；入口预览仍保留原来的五个根。

`watch` 在终端持续显示已提交的声明和引用数。`serve` 启动仅监听本机的实时进度面板，并输出访问地址；页面每 3 秒刷新。百分比是已展开声明占全项目目录的比例，不是根声明闭包的完成百分比。服务器以只读模式读取数据库。

完成导出后，同一服务器的 `/graph` 路径展示交互依赖图预览。

## 最长依赖路径分层

在依赖完整且无环的图上，指定基础声明集合 B，令基础声明层级为 0，其余声明为 `1 + max(依赖声明的层级)`。这衡量形式化依赖深度，不能直接等同于数学难度。Mathlib 的全部声明不能一律算作基础层；需要继续追踪到明确约定的核心定义、公理或基础声明。

当前 `layers` 命令计算的是候选图的结构高度：先把强连通分量收缩为一个节点，再用最长路径递推。它输出 `candidate-layers.jsonl` 和 `layer-summary.json`。候选叶子的高度 0 仅表示没有已记录的项目内部依赖，不表示数学基础层。循环分量内的节点共享一个结构层级，不能解释为原始图上的有限最长路径。

分层输出中的 `in_candidate_cycle` 与 `cycle_audit_status` 显式标记需要审计的循环分量。收缩只定义分量图的结构高度，不解决循环论证。未处于循环分量的节点仍可能依赖需要审计的节点，也不代表其证明已经通过验证。

旧版解析器曾将后续匿名 `example` 的引用归入前一条声明，产生自环误报；现已加入命令边界。对旧目录可运行 `python run.py repair-examples --db artifacts/dependencies.sqlite --out artifacts`（需 ripgrep），保留声明 ID 并撤销受影响的旧引用，然后再运行 `expand --all`。修复报告为 `example-boundary-repair.json`。

所有节点的 `mathlib_level` 暂为 null。项目指定版本的 Mathlib 尚未接入，隐式参数、类型类实例和宏展开依赖也未从 Lean 环境导出。因此候选高度既不是 Mathlib 真实层级，也不保证是真实层级的上下界。下一阶段需要从匹配工具链的 elaborated environment 导出常量引用，并确定基础集合 B，再计算 Mathlib 锚定层级。

例如 `Tm_prime_inj` 的显式引用包括 `Function.Injective` 与 `Prod.mk.injEq`；它在当前项目候选图中是叶子，但真实 Mathlib 层级未知。不能因为缺边就将其认定为第 0 层。

```powershell
$env:PYTHONPATH = "$PWD/src"
python -m unittest discover -s tests -v
```

## 输出

| 文件 | 内容 |
|---|---|
| `artifacts/dependencies.sqlite` | 全量符号目录、模块导入、已展开候选引用、未解析项及运行状态 |
| `artifacts/summary.json` | 数量、版本、闭包状态、未解析原因 |
| `artifacts/candidate-nodes.jsonl` | 已展开声明，含源码定位与声明头 |
| `artifacts/candidate-edges.jsonl` | 候选边、阶段、引用证据与定位 |
| `artifacts/module-imports.jsonl` | 全项目模块导入图，显式区分外部模块 |
| `artifacts/main-preview.json` | 限深节点、边及 frontier |
| `artifacts/main-preview.dot` | Graphviz 预览，虚线表示候选边 |
| `artifacts/main-preview.html` | 无网络依赖的交互预览，支持搜索、缩放和点击节点 |
| `artifacts/candidate-cycles.json` | 候选图的循环分量；不能直接解释成逻辑缺陷 |

SQLite 主表为 `modules`、`imports`、`declarations`、`edges`、`unresolved`、`expanded`、`roots`；`metadata` 记录构建状态。节点 ID 区分不同来源的同名声明。不要只用显示名称合并节点。
