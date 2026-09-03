# -*- coding: utf-8 -*-
"""One-off script to localize index.zh.html from English template."""
from pathlib import Path

path = Path(__file__).parent / "index.zh.html"
text = path.read_text(encoding="utf-8")

# --- head / fonts ---
text = text.replace('<html lang="en">', '<html lang="zh-CN">')
text = text.replace(
    "<title>Mendel Gödel Machine</title>",
    "<title>孟德尔哥德尔机器 · Mendel Gödel Machine</title>",
)
text = text.replace(
    'content="Mendel Gödel Machine (MGM): self-improving coding agents that edit their own scaffolds using Mendelian comparative evolution — clonal mutation, reaction-norm mutation, and cross-lineage hybridization."',
    'content="孟德尔哥德尔机器（MGM）：通过孟德尔式比较进化实现递归自我改进的编程智能体——克隆突变、反应规范突变与跨谱系杂交。"',
)
text = text.replace(
    'content="Recursive self-improving coding agents via comparative evolution. 50.8% → 93.3% on Polyglot with a 35B open model — surpassing GPT-5."',
    'content="基于比较进化的递归自我改进编程智能体。35B 开源模型在 Polyglot 上 50.8% → 93.3%，超越 GPT-5。"',
)
text = text.replace(
    "family=IBM+Plex+Mono:wght@400;500;600&display=swap",
    "family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+SC:wght@400;500;600;700&display=swap",
)
text = text.replace(
    "--body:'Overpass',system-ui,sans-serif;",
    "--body:'Overpass','Noto Sans SC','PingFang SC','Microsoft YaHei',system-ui,sans-serif;",
)

# zh typography tweaks (append before </style>)
zh_css = """
html:lang(zh-CN) .slide.bg .bg-panel h3,
html:lang(zh-CN) .slide.bg .op h3{text-transform:none;letter-spacing:.04em;}
html:lang(zh-CN) .navlinks a{text-transform:none;}
"""
text = text.replace("</style>", zh_css + "</style>", 1)

# --- ordered replacements (longer first where needed) ---
pairs = [
    # cover
    (
        "<h1 class=\"subhead rv\"><i>Recursive Self-Improving Coding Agents via Comparative Evolution</i></h1>",
        "<h1 class=\"subhead rv\"><i>基于比较进化的递归自我改进编程智能体</i></h1>",
    ),
    (
        "<sup>§</sup>Equal contribution",
        "<sup>§</sup>同等贡献",
    ),
    # archive
    (
        'aria-label="Interactive MGM evolution tree"',
        'aria-label="MGM 交互式进化树"',
    ),
    ("<span><i class=\"mgm-line cm\"></i>Clonal Mutation</span>", "<span><i class=\"mgm-line cm\"></i>克隆突变</span>"),
    ("<span><i class=\"mgm-line rm\"></i>Reaction-norm Mutation</span>", "<span><i class=\"mgm-line rm\"></i>反应规范突变</span>"),
    ("<span><i class=\"mgm-line ch\"></i>Cross-lineage Hybridization</span>", "<span><i class=\"mgm-line ch\"></i>跨谱系杂交</span>"),
    ("<span><i class=\"mgm-line ref\"></i>Hybridization reference</span>", "<span><i class=\"mgm-line ref\"></i>杂交参考</span>"),
    ("<span><i class=\"mgm-dot util\"></i>Utility</span>", "<span><i class=\"mgm-dot util\"></i>效用</span>"),
    (
        "<b>Evolution tree of MGM.</b> Results show the archive evolved with 200 φ-evaluations and 24 Φ-expansions on Polyglot, with nodes colored by utility estimates aggregated over accumulated φ-evaluation results, and edges corresponding with different Φ-expansion operators.",
        "<b>MGM 进化树。</b> 在 Polyglot 上经 200 次 φ-评估与 24 次 Φ-扩展进化所得档案；节点颜色为累积 φ-评估结果的效用估计，边对应不同 Φ-扩展算子。",
    ),
    # bg-rsi
    (
        "<h2 class=\"rv\">Recursive Self-Improvement: <span class=\"leaf\">Vision</span></h2>",
        "<h2 class=\"rv\">递归自我改进：<span class=\"leaf\">愿景</span></h2>",
    ),
    ("<h3>Historical vision</h3>", "<h3>历史愿景</h3>"),
    (
        "<li>AI recursively rewriting itself to become better (<a class=\"cite\" href=\"https://mediatum.ub.tum.de/?id=813180\" target=\"_blank\" rel=\"noopener noreferrer\">Schmidhuber, 1987</a>).</li>",
        "<li>AI 递归改写自身以持续变强（<a class=\"cite\" href=\"https://mediatum.ub.tum.de/?id=813180\" target=\"_blank\" rel=\"noopener noreferrer\">Schmidhuber, 1987</a>）。</li>",
    ),
    (
        "<li><b>Gödel Machine</b> (<a class=\"cite\" href=\"http://arxiv.org/abs/cs/0309048\" target=\"_blank\" rel=\"noopener noreferrer\">2003</a>, <a class=\"cite\" href=\"https://doi.org/10.1007/978-3-540-68677-4_7\" target=\"_blank\" rel=\"noopener noreferrer\">2007</a>): rigorous self-referential modification when a provable benefit can be derived.</li>",
        "<li><b>哥德尔机器</b>（<a class=\"cite\" href=\"http://arxiv.org/abs/cs/0309048\" target=\"_blank\" rel=\"noopener noreferrer\">2003</a>，<a class=\"cite\" href=\"https://doi.org/10.1007/978-3-540-68677-4_7\" target=\"_blank\" rel=\"noopener noreferrer\">2007</a>）：在可证明收益时进行严格的自指修改。</li>",
    ),
    (
        "<li>LLMs and coding agents now empirically approximate this loop (<a class=\"cite\" href=\"https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">Yang et al., 2024</a>; <a class=\"cite\" href=\"https://proceedings.iclr.cc/paper_files/paper/2025/file/36b7acf6f6010652b3f2a433774a66fe-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">Hu et al., 2024</a>; <a class=\"cite\" href=\"https://openreview.net/forum?id=CTr3bovS5F\" target=\"_blank\" rel=\"noopener noreferrer\">Gao et al., 2026</a>).</li>",
        "<li>大语言模型与编程智能体已在经验上逼近这一闭环（<a class=\"cite\" href=\"https://proceedings.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">Yang et al., 2024</a>；<a class=\"cite\" href=\"https://proceedings.iclr.cc/paper_files/paper/2025/file/36b7acf6f6010652b3f2a433774a66fe-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">Hu et al., 2024</a>；<a class=\"cite\" href=\"https://openreview.net/forum?id=CTr3bovS5F\" target=\"_blank\" rel=\"noopener noreferrer\">Gao et al., 2026</a>）。</li>",
    ),
    ("<h3>Empirical RSI loop</h3>", "<h3>经验性 RSI 闭环</h3>"),
    (
        "<p>Agent <b>edits its own source code</b> (prompts, tools, control logic, auxiliary routines).</p>",
        "<p>智能体<b>编辑自身源代码</b>（提示、工具、控制逻辑与辅助例程）。</p>",
    ),
    (
        "<p>Change is <b>accepted if measured benchmark performance improves</b>—a practical Gödel-style accept/reject criterion.</p>",
        "<p>若基准性能提升则<b>接受改动</b>——一种实用的哥德尔式接受/拒绝准则。</p>",
    ),
    ("<p>Each iteration: evaluate → diagnose → modify → re-evaluate.</p>", "<p>每轮迭代：评估 → 诊断 → 修改 → 再评估。</p>"),
    # bg-rsi-progress
    (
        "<h2 class=\"rv\">Empirical RSI: <span class=\"leaf\">Recent Progress</span></h2>",
        "<h2 class=\"rv\">经验性 RSI：<span class=\"leaf\">近期进展</span></h2>",
    ),
    (
        "<p>Agent with file-editing tools autonomously <b>refactors its own codebase</b> and lifts <a class=\"cite\" href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench</a> / <a class=\"cite\" href=\"https://openreview.net/forum?id=chfJJYC3iL\" target=\"_blank\" rel=\"noopener noreferrer\">LiveCodeBench</a> performance.</p>",
        "<p>具备文件编辑工具的智能体自主<b>重构自身代码库</b>，提升 <a class=\"cite\" href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench</a> / <a class=\"cite\" href=\"https://openreview.net/forum?id=chfJJYC3iL\" target=\"_blank\" rel=\"noopener noreferrer\">LiveCodeBench</a> 表现。</p>",
    ),
    (
        "<p>Reframes RSI as <b>open-ended evolution</b>: an expanding archive of variants; each iteration samples one parent to seed the next self-modification.</p>",
        "<p>将 RSI 重构为<b>开放式进化</b>：不断扩展的变体档案；每轮采样一个父代以启动下一次自我修改。</p>",
    ),
    (
        r"<p>Casts archive maintenance as <b>fixed-budget tree search</b>; uses descendant performance (clade evidence) to guide \(\varphi\)-evaluation and \(\Phi\)-expansion.</p>",
        r"<p>将档案维护视为<b>固定预算树搜索</b>；用后代表现（分支证据）指导 \(\varphi\)-评估与 \(\Phi\)-扩展。</p>",
    ),
    (
        "<p class=\"bg-strip rv\"><b>Progress so far</b> has focused on optimizing the <b>archive</b> (what to store) and the <b>\\(\\pi\\)-sampling policy</b> (which agent to evaluate or edit next)—not on how the edit itself is conditioned.</p>",
        "<p class=\"bg-strip rv\"><b>迄今进展</b>主要优化<b>档案</b>（存什么）与<b>\\(\\pi\\)-采样策略</b>（下一步评估或编辑哪个智能体）——而非编辑本身如何被条件化。</p>",
    ),
    # bg-formal
    (
        "<h2 class=\"rv\">Formal Setup: <span class=\"leaf\">Genotype &amp; Archive</span></h2>",
        "<h2 class=\"rv\">形式化设定：<span class=\"leaf\">基因型与档案</span></h2>",
    ),
    ("<h3>Agent as genotype · task behavior as phenotype</h3>", "<h3>智能体即基因型 · 任务行为即表型</h3>"),
    (
        "<p>Coding agent \\(a\\in\\mathcal{A}\\) (prompts, tools, control logic) is the <b>genotype</b>. Running \\(a\\) on \\(\\tau\\sim\\mathcal{D}\\) yields trajectory \\(\\varphi(a,\\tau)\\) and outcome \\(r(a,\\tau)\\in\\{0,1\\}\\)—the <b>phenotype</b>.</p>",
        "<p>编程智能体 \\(a\\in\\mathcal{A}\\)（提示、工具、控制逻辑）为<b>基因型</b>。在 \\(\\tau\\sim\\mathcal{D}\\) 上运行 \\(a\\) 得轨迹 \\(\\varphi(a,\\tau)\\) 与结果 \\(r(a,\\tau)\\in\\{0,1\\}\\)——即<b>表型</b>。</p>",
    ),
    (
        "<p>Self-modification \\(\\Phi\\) edits code using evidence \\(E\\subseteq\\{(\\varphi(a,\\tau),r(a,\\tau))\\}\\).</p>",
        "<p>自我修改 \\(\\Phi\\) 依据证据 \\(E\\subseteq\\{(\\varphi(a,\\tau),r(a,\\tau))\\}\\) 编辑代码。</p>",
    ),
    ("<h3>Archive tree · node statistics</h3>", "<h3>档案树 · 节点统计</h3>"),
    (
        "<p><a class=\"cite\" href=\"https://openreview.net/forum?id=pUpzQZTvGY\" target=\"_blank\" rel=\"noopener noreferrer\">DGM</a> maintains expanding tree \\(\\mathcal{G}_t\\); <a class=\"cite\" href=\"https://openreview.net/forum?id=T0EiEuhOOL\" target=\"_blank\" rel=\"noopener noreferrer\">HGM</a> treats maintenance as fixed-budget search over nodes \\(\\mathcal{V}_t\\).</p>",
        "<p><a class=\"cite\" href=\"https://openreview.net/forum?id=pUpzQZTvGY\" target=\"_blank\" rel=\"noopener noreferrer\">DGM</a> 维护扩展树 \\(\\mathcal{G}_t\\)；<a class=\"cite\" href=\"https://openreview.net/forum?id=T0EiEuhOOL\" target=\"_blank\" rel=\"noopener noreferrer\">HGM</a> 将维护视为节点 \\(\\mathcal{V}_t\\) 上的固定预算搜索。</p>",
    ),
    (
        "<p>For node \\(a_i\\): evaluated tasks \\(S_i\\), failures \\(F_i\\subseteq S_i\\). Clade \\(C_t(a)\\) is the subtree rooted at \\(a\\).</p>",
        "<p>节点 \\(a_i\\)：已评估任务 \\(S_i\\)，失败集 \\(F_i\\subseteq S_i\\)。分支 \\(C_t(a)\\) 为以 \\(a\\) 为根的子树。</p>",
    ),
    # bg-hgm
    (
        "<h2 class=\"rv\">HGM Baseline: <span class=\"leaf\">\\(\\pi\\)-Policy</span></h2>",
        "<h2 class=\"rv\">HGM 基线：<span class=\"leaf\">\\(\\pi\\)-策略</span></h2>",
    ),
    (
        "<h3><a class=\"cite\" href=\"https://openreview.net/forum?id=T0EiEuhOOL\" target=\"_blank\" rel=\"noopener noreferrer\">HGM</a> · evaluation vs. expansion</h3>",
        "<h3><a class=\"cite\" href=\"https://openreview.net/forum?id=T0EiEuhOOL\" target=\"_blank\" rel=\"noopener noreferrer\">HGM</a> · 评估 vs. 扩展</h3>",
    ),
    (
        "<p>When total evaluations \\(N_t\\) exceed budget threshold \\(N_t^\\alpha\\), the policy switches from \\(\\varphi\\)-evaluation to \\(\\Phi\\)-expansion; both stages use Beta posteriors over node or clade success rates.</p>",
        "<p>当总评估数 \\(N_t\\) 超过预算阈值 \\(N_t^\\alpha\\) 时，策略从 \\(\\varphi\\)-评估切换至 \\(\\Phi\\)-扩展；两阶段均对节点或分支成功率使用 Beta 后验。</p>",
    ),
    (
        "<p class=\"bg-note rv\"><b>MGM inherits this archive &amp; \\(\\pi\\)-sampling backbone</b>—the difference is how \\(\\Phi\\) constructs evidence \\(E\\) from comparative trajectories.</p>",
        "<p class=\"bg-note rv\"><b>MGM 继承此档案与 \\(\\pi\\)-采样骨架</b>——差异在于 \\(\\Phi\\) 如何从比较轨迹构造证据 \\(E\\)。</p>",
    ),
    # bg-gap
    (
        "<h2 class=\"rv\">The Gap: <span class=\"leaf\">Self-Modification</span> Itself</h2>",
        "<h2 class=\"rv\">缺口：<span class=\"leaf\">自我修改</span>本身</h2>",
    ),
    ("<h3>What has been optimized</h3>", "<h3>已优化部分</h3>"),
    (
        "<li><b>Archive structure</b>: lineage tree of all generated agents + stored trajectories.</li>",
        "<li><b>档案结构</b>：所有生成智能体的谱系树与存储轨迹。</li>",
    ),
    (
        "<li><b>\\(\\pi\\)-sampling</b>: when to evaluate (\\(\\varphi\\)) vs. expand (\\(\\Phi\\)); which node/clade to allocate budget to.</li>",
        "<li><b>\\(\\pi\\)-采样</b>：何时评估（\\(\\varphi\\)）vs. 扩展（\\(\\Phi\\)）；向哪个节点/分支分配预算。</li>",
    ),
    (
        "<li><b>Selection heuristics</b>: Thompson sampling, clade-level posteriors, widening criteria.</li>",
        "<li><b>选择启发式</b>：汤普森采样、分支级后验、扩展准则。</li>",
    ),
    ("<h3>What remains underexplored</h3>", "<h3>尚待探索</h3>"),
    (
        "<li>The <b>\\(\\Phi\\) self-modification process</b>—how the agent actually edits its code given evidence \\(E\\).</li>",
        "<li><b>\\(\\Phi\\) 自我修改过程</b>——智能体如何依据证据 \\(E\\) 实际编辑代码。</li>",
    ),
    (
        "<li>Each edit conditioned on <b>one agent × one trajectory × one task</b> (typically a recent failure).</li>",
        "<li>每次编辑仅条件于<b>一个智能体 × 一条轨迹 × 一个任务</b>（通常是近期失败）。</li>",
    ),
    (
        "<li>Archive used as a <b>leaderboard for sampling</b>, not as comparative evidence for richer edits.</li>",
        "<li>档案仅作<b>采样排行榜</b>，未作为更丰富编辑的比较证据。</li>",
    ),
    (
        "<p class=\"bg-strip rv\">The archive already stores cross-task and cross-lineage trajectories—<b>MGM</b> reuses them as comparative evidence for \\(\\Phi\\), at <b>zero extra evaluation cost</b>.</p>",
        "<p class=\"bg-strip rv\">档案已存储跨任务与跨谱系轨迹——<b>MGM</b> 将其复用为 \\(\\Phi\\) 的比较证据，<b>零额外评估成本</b>。</p>",
    ),
    # bg-signals
    (
        "<h2 class=\"rv\">Comparative Signals for <span class=\"leaf\">Controlled Inheritance</span></h2>",
        "<h2 class=\"rv\">受控遗传的<span class=\"leaf\">比较信号</span></h2>",
    ),
    ("<h3>Signal I · Reaction norm</h3>", "<h3>信号 I · 反应规范</h3>"),
    (
        "<p>When an agent is evaluated across tasks, its success/failure <b>pattern forms a <a class=\"cite\" href=\"https://cir.nii.ac.jp/crid/1573668924237108864\" target=\"_blank\" rel=\"noopener noreferrer\">reaction norm</a></b>—a genotype-specific performance profile across environments.</p>",
        "<p>跨任务评估时，成败<b>模式形成 <a class=\"cite\" href=\"https://cir.nii.ac.jp/crid/1573668924237108864\" target=\"_blank\" rel=\"noopener noreferrer\">反应规范</a></b>——基因型在不同环境下的表现轮廓。</p>",
    ),
    (
        "<p>Recurring failure modes distinguish <b>genotype-level defects</b> from task-specific accidents.</p>",
        "<p>反复出现的失败模式可区分<b>基因型级缺陷</b>与任务特异性偶然。</p>",
    ),
    ("<h3>Signal II · Cross-lineage contrast</h3>", "<h3>信号 II · 跨谱系对比</h3>"),
    (
        "<p>When multiple agents attempt the <b>same task</b>, their trajectories reveal transferable behavioral traits.</p>",
        "<p>多个智能体尝试<b>同一任务</b>时，轨迹揭示可迁移的行为特征。</p>",
    ),
    (
        "<p>Contrastive evidence enables <b>targeted ability transfer</b> across lineages and reduces redundant exploration.</p>",
        "<p>对比证据支持跨谱系<b>定向能力迁移</b>，减少冗余探索。</p>",
    ),
    (
        "<p class=\"bg-strip rv\">Following Mendelian principles of controlled comparison, MGM maps these signals to three \\(\\Phi\\) operators (CM / RM / CH).</p>",
        "<p class=\"bg-strip rv\">遵循孟德尔受控比较原则，MGM 将这些信号映射为三种 \\(\\Phi\\) 算子（CM / RM / CH）。</p>",
    ),
    # bg-operators
    (
        "<h2 class=\"rv\">Evolution with <br><span class=\"leaf\">Controlled Inheritance.</span></h2>",
        "<h2 class=\"rv\">受控遗传的<br><span class=\"leaf\">进化。</span></h2>",
    ),
    (
        "<p class=\"lede rv\">MGM partitions the expansion operator \\(\\Phi\\) into three specialized sub-operators—\\(\\Phi_{\\mathrm{CM}}\\), \\(\\Phi_{\\mathrm{RM}}\\), and \\(\\Phi_{\\mathrm{CH}}\\)—each driven by a distinct diagnostic evidence regime \\(E\\) in the archive.</p>",
        "<p class=\"lede rv\">MGM 将扩展算子 \\(\\Phi\\) 划分为三个专用子算子——\\(\\Phi_{\\mathrm{CM}}\\)、\\(\\Phi_{\\mathrm{RM}}\\) 与 \\(\\Phi_{\\mathrm{CH}}\\)——各自由档案中不同的诊断证据 \\(E\\) 驱动。</p>",
    ),
    ("<h3><i>CM</i> Clonal Mutation</h3>", "<h3><i>CM</i> 克隆突变</h3>"),
    (
        "<p>Standard <b>single-agent, single-trajectory</b> self-modification when only one informative failure is available.</p>",
        "<p>仅有一个有效失败时，标准的<b>单智能体、单轨迹</b>自我修改。</p>",
    ),
    ("<h3><i>RM</i> Reaction-norm Mutation</h3>", "<h3><i>RM</i> 反应规范突变</h3>"),
    (
        "<p><b>Same genotype, multiple tasks</b>: compare phenotypes across environments to expose genotype-level weaknesses.</p>",
        "<p><b>同基因型、多任务</b>：跨环境比较表型，暴露基因型级弱点。</p>",
    ),
    ("<h3><i>CH</i> Cross-lineage Hybridization</h3>", "<h3><i>CH</i> 跨谱系杂交</h3>"),
    (
        "<p><b>Different genotypes, shared task</b>: contrast trajectories to transfer transferable behavioral traits.</p>",
        "<p><b>不同基因型、共享任务</b>：对比轨迹以迁移可迁移的行为特征。</p>",
    ),
    # bg-mgm-policy
    (
        "<h2 class=\"rv\">MGM · <span class=\"leaf\">Sampling &amp; Selection</span></h2>",
        "<h2 class=\"rv\">MGM · <span class=\"leaf\">采样与选择</span></h2>",
    ),
    ("<h3>Failed-task pool · evaluation sampling</h3>", "<h3>失败任务池 · 评估采样</h3>"),
    (
        "<p>MGM maintains a global pool of tasks that exposed failures in any evaluated agent—reused for sampling, with <b>no extra \\(\\varphi\\)-evaluations</b>.</p>",
        "<p>MGM 维护全局失败任务池（任一已评估智能体暴露的失败）——用于采样复用，<b>无额外 \\(\\varphi\\)-评估</b>。</p>",
    ),
    (
        "<p>Untried tasks for agent \\(i\\) are weighted toward failures already seen in the archive:</p>",
        "<p>智能体 \\(i\\) 的未尝试任务向档案中已见失败倾斜加权：</p>",
    ),
    ("<h3>Operator eligibility · \\(\\Phi\\)-expansion</h3>", "<h3>算子资格 · \\(\\Phi\\)-扩展</h3>"),
    (
        "<p>When \\(\\pi\\) selects \\(\\Phi\\)-expansion for parent \\(a_i\\), MGM builds eligible set \\(\\Omega_i\\subseteq\\{\\varPhi_{\\mathrm{CM}},\\varPhi_{\\mathrm{RM}},\\varPhi_{\\mathrm{CH}}\\}\\) from archive evidence and samples among them:</p>",
        "<p>当 \\(\\pi\\) 为父代 \\(a_i\\) 选择 \\(\\Phi\\)-扩展时，MGM 据档案证据构造 eligible 集 \\(\\Omega_i\\subseteq\\{\\varPhi_{\\mathrm{CM}},\\varPhi_{\\mathrm{RM}},\\varPhi_{\\mathrm{CH}}\\}\\) 并采样：</p>",
    ),
    (
        "<p class=\"bg-note rv\"><b>MGM inherits HGM's Thompson-sampling policy \\(\\pi\\)</b> for evaluate-vs-expand and node/clade selection—the difference is how \\(\\Phi\\)-expansion is partitioned and executed via \\(\\Omega_i\\).</p>",
        "<p class=\"bg-note rv\"><b>MGM 继承 HGM 的汤普森采样策略 \\(\\pi\\)</b>（评估 vs. 扩展及节点/分支选择）——差异在于 \\(\\Phi\\)-扩展如何通过 \\(\\Omega_i\\) 划分与执行。</p>",
    ),
    # method
    (
        "<h2 class=\"rv\">MGM <span class=\"leaf\">Overview.</span></h2>",
        "<h2 class=\"rv\">MGM <span class=\"leaf\">总览。</span></h2>",
    ),
    (
        'alt="Mendel Gödel Machine overview: archive lineage tree, sampling–evaluation–expansion loop, and three self-modification operators"',
        'alt="孟德尔哥德尔机器总览：档案谱系树、采样-评估-扩展循环与三种自我修改算子"',
    ),
    (
        "<b>Mendel Gödel Machine.</b> MGM organizes self-modification via controlled inheritance based on evidence across tasks and lineages.\n            The archive maintains a lineage tree of agent variants; each stores its source code as genotype and evaluation outcomes as phenotype.\n            Each iteration applies π-sampling that selects an operation to perform with the necessary resources from the archive.\n            φ-evaluation executes the agent on untested tasks and records its trajectory and results.\n            Clonal mutation Φ<sub>CM</sub> edits the agent based on a single failure trajectory on target task τ<sub>t</sub>.\n            Reaction-norm mutation Φ<sub>RM</sub> uses the agent's trajectories across reference tasks τ<sub>r</sub>.\n            Cross-lineage hybridization Φ<sub>CH</sub> uses a reference agent a<sub>r</sub> from a different lineage that attempted the same task.",
        "<b>孟德尔哥德尔机器。</b> MGM 基于跨任务与跨谱系证据，通过受控遗传组织自我修改。\n            档案维护智能体变体谱系树；各节点以源码为基因型、评估结果为表型。\n            每轮迭代以 π-采样从档案选取操作与资源。\n            φ-评估在未测任务上运行智能体并记录轨迹与结果。\n            克隆突变 Φ<sub>CM</sub> 依据目标任务 τ<sub>t</sub> 的单条失败轨迹编辑。\n            反应规范突变 Φ<sub>RM</sub> 利用参考任务 τ<sub>r</sub> 上的多条轨迹。\n            跨谱系杂交 Φ<sub>CH</sub> 引用尝试同一任务的其他谱系参考智能体 a<sub>r</sub>。",
    ),
    # results
    (
        "<h2 class=\"rv\">Bringing Open Models<br>\n          <span class=\"gold leaf\">Beyond Frontier</span> Performance.</h2>",
        "<h2 class=\"rv\">开源模型<br>\n          <span class=\"gold leaf\">超越前沿</span>性能。</h2>",
    ),
    (
        "<p class=\"lede rv\">MGM's reuse of archived trajectories through reaction-norm mutation and cross-lineage hybridization provides more informative guidance for self-modification, yielding substantially and consistently better performance than HGM baseline without necessitating any additional task evaluations and token expenditure.</p>",
        "<p class=\"lede rv\">MGM 通过反应规范突变与跨谱系杂交复用档案轨迹，为自我修改提供更丰富指导，在无需额外任务评估与 token 开销下，显著且稳定优于 HGM 基线。</p>",
    ),
    (
        'aria-label="Polyglot performance versus model size. Qwen3.6-35B-A3B plus MGM reaches 93.3 percent; DeepSeek-V4-Pro plus MGM reaches 96.9 percent."',
        'aria-label="Polyglot 性能与模型规模。Qwen3.6-35B-A3B + MGM 达 93.3%；DeepSeek-V4-Pro + MGM 达 96.9%。"',
    ),
    ("<span><i class=\"swatch open\"></i>Open-source</span>", "<span><i class=\"swatch open\"></i>开源</span>"),
    ("<span><i class=\"swatch closed\"></i>Closed-source</span>", "<span><i class=\"swatch closed\"></i>闭源</span>"),
    ("<span><i class=\"swatch ours\"></i>Self-improving</span>", "<span><i class=\"swatch ours\"></i>自我改进</span>"),
    (
        "<b>Performance of coding agents on Polyglot and model sizes.</b> Result marked with asterisk is from Polyglot-60; all other scores are from the complete Polyglot‑225. Performance of <a href=\"https://arxiv.org/abs/2606.19348\" target=\"_blank\" rel=\"noopener noreferrer\">Deepseek-V4-Pro</a> is the transfered result with the scaffold evolved on <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a>. Performance for models outside our experiments are sourced directly from the official <a href=\"https://llm-stats.com/benchmarks/aider-polyglot\" target=\"_blank\" rel=\"noopener noreferrer\">Aider-Polyglot Leaderboard</a>.\n            Closed-source model sizes follows estimates reported by <a href=\"https://arxiv.org/abs/2604.24827\" target=\"_blank\" rel=\"noopener noreferrer\">Li et al. (2026)</a>.",
        "<b>Polyglot 上编程智能体性能与模型规模。</b> 带星号结果为 Polyglot-60；其余为完整 Polyglot-225。<a href=\"https://arxiv.org/abs/2606.19348\" target=\"_blank\" rel=\"noopener noreferrer\">Deepseek-V4-Pro</a> 为在 <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a> 上进化脚手架后的迁移结果。实验外模型性能来自官方 <a href=\"https://llm-stats.com/benchmarks/aider-polyglot\" target=\"_blank\" rel=\"noopener noreferrer\">Aider-Polyglot 排行榜</a>。闭源模型规模采用 <a href=\"https://arxiv.org/abs/2604.24827\" target=\"_blank\" rel=\"noopener noreferrer\">Li et al. (2026)</a> 估计。",
    ),
    ("<p class=\"l\">Performance</p>", "<p class=\"l\">性能</p>"),
    ("<p class=\"l\">Relative improvement</p>", "<p class=\"l\">相对提升</p>"),
    ("<p class=\"l\">Average Performance</p>", "<p class=\"l\">平均性能</p>"),
    ("<th>Agent</th>", "<th>智能体</th>"),
    ("<th>Initial</th>", "<th>初始</th>"),
    ("<th class=\"group\" colspan=\"3\">Average</th>", "<th class=\"group\" colspan=\"3\">平均</th>"),
    ("<td class=\"num\">Accuracy</td>", "<td class=\"num\">准确率</td>"),
    ("<td class=\"num\">% Impr.</td>", "<td class=\"num\">提升%</td>"),
    ("<td class=\"num\">Time</td>", "<td class=\"num\">时间</td>"),
    (
        "<b>Performance of coding agents evolved on <a href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Verified</a> and <a href=\"https://aider.chat/2024/12/21/polyglot.html\" target=\"_blank\" rel=\"noopener noreferrer\">Polyglot</a>.</b>\n          Results evolved using <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a> after 200 φ-evaluations and 24 Φ-expansions.\n          For each benchmark, HGM and MGM start from the same initial scaffold.\n          Superscripts in accuracy denote absolute percentage-point improvements over corresponding initial agents.\n          Time reported as CPU wall-clock time with 8×NVIDIA H100 GPUs.",
        "<b>在 <a href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Verified</a> 与 <a href=\"https://aider.chat/2024/12/21/polyglot.html\" target=\"_blank\" rel=\"noopener noreferrer\">Polyglot</a> 上进化的编程智能体性能。</b>\n          使用 <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a>，经 200 次 φ-评估与 24 次 Φ-扩展。各基准上 HGM 与 MGM 从相同初始脚手架出发。准确率上标为相对初始智能体的绝对百分点提升。时间为 8×NVIDIA H100 GPU 的 CPU 墙钟时间。",
    ),
    # generalization
    (
        "<h2 class=\"rv\"><span class=\"leaf\">Genuinely Reusable</span> <br>Agent Scaffolds.</h2>",
        "<h2 class=\"rv\"><span class=\"leaf\">真正可复用</span><br>的智能体脚手架。</h2>",
    ),
    (
        "<p class=\"lede rv\">MGM-evolved scaffolds can transfer zero-shot from standalone coding tasks (Polyglot) to repository-level software-engineering benchmarks (SWE-bench Pro and SWE-bench Multilingual) with significant gains over the baseline.</p>",
        "<p class=\"lede rv\">MGM 进化脚手架可零样本从独立编程任务（Polyglot）迁移至仓库级软件工程基准（SWE-bench Pro 与 SWE-bench Multilingual），相对基线显著提升。</p>",
    ),
    (
        "<p class=\"lede rv\">MGM also generalizes better across backbone models when scaffolds evolved on Qwen3.6-35B-A3B are switched to working with significantly larger DeepSeek-V4-Flash or DeepSeek-V4-Pro.</p>",
        "<p class=\"lede rv\">在 Qwen3.6-35B-A3B 上进化的脚手架切换至更大骨干 DeepSeek-V4-Flash 或 DeepSeek-V4-Pro 时，MGM 跨模型泛化更优。</p>",
    ),
    (
        "<p class=\"lede rv\">These results demonstrate that MGM's comparative evolution can help discover genuinely reusable workflow-level improvements rather than benchmark-specific or model-specific hacks, pointing toward scalable self-improving agent development where scaffolds evolved on smaller datasets and cheaper backbones can be reused on stronger models.</p>",
        "<p class=\"lede rv\">结果表明 MGM 的比较进化有助于发现真正可复用的工作流级改进，而非基准或模型特化技巧——指向可扩展的自我改进智能体开发：在小数据集与廉价骨干上进化的脚手架可复用于更强模型。</p>",
    ),
    (
        "<h2 class=\"rv\"><span class=\"leaf\">Transfer</span> <br>Across Benchmarks &amp; Models.</h2>",
        "<h2 class=\"rv\"><span class=\"leaf\">跨基准</span><br>与跨模型迁移。</h2>",
    ),
    (
        "<p class=\"hs-tag\">Cross-benchmark<br><i>Polyglot → SWE-bench Pro &amp; Multilingual</i></p>",
        "<p class=\"hs-tag\">跨基准<br><i>Polyglot → SWE-bench Pro &amp; Multilingual</i></p>",
    ),
    (
        "<p class=\"hs-tag\">Cross-model<br><i>Qwen3.6-35B-A3B → DeepSeek-V4-Flash &amp; Pro</i></p>",
        "<p class=\"hs-tag\">跨模型<br><i>Qwen3.6-35B-A3B → DeepSeek-V4-Flash &amp; Pro</i></p>",
    ),
    (
        "<b>Cross-benchmark generalization from <a href=\"https://aider.chat/2024/12/21/polyglot.html\" target=\"_blank\" rel=\"noopener noreferrer\">Polyglot</a> to <a href=\"https://arxiv.org/abs/2509.16941\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Pro</a> and <a href=\"https://www.swebench.com/multilingual.html\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Multilingual</a>.</b>\n          All agents evolved on Polyglot are evaluated zero-shot on held-out SWE-bench variants, using <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a>.\n          Superscripts in accuracy denote absolute percentage-point improvements over corresponding initial agents.",
        "<b>从 <a href=\"https://aider.chat/2024/12/21/polyglot.html\" target=\"_blank\" rel=\"noopener noreferrer\">Polyglot</a> 到 <a href=\"https://arxiv.org/abs/2509.16941\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Pro</a> 与 <a href=\"https://www.swebench.com/multilingual.html\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Multilingual</a> 的跨基准泛化。</b>\n          在 Polyglot 上进化的智能体以 <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a> 零样本评估于 held-out SWE-bench 变体。准确率上标为相对初始智能体的绝对百分点提升。",
    ),
    ("<th class=\"group\" colspan=\"3\"><i>Evolved</i></th>", "<th class=\"group\" colspan=\"3\"><i>进化</i></th>"),
    ("<th class=\"group\" colspan=\"9\"><i>Transferred</i></th>", "<th class=\"group\" colspan=\"9\"><i>迁移</i></th>"),
    ("<th>Init.</th>", "<th>初始</th>"),
    ("<td class=\"num\">Acc.</td>", "<td class=\"num\">准确率</td>"),
    (
        "<b>Cross-model transfer on <a href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Verified-60</a>.</b>\n          The <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a> block reports the original evolved agents, while the <a href=\"https://arxiv.org/abs/2606.19348\" target=\"_blank\" rel=\"noopener noreferrer\">DeepSeek</a> blocks report transferred performance by using the scaffolds evolved on Qwen3.6-35B-A3B and evaluating with LLM backbone replaced.\n          Superscripts in accuracy denote absolute percentage-point improvements over corresponding initial agents.",
        "<b>在 <a href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Verified-60</a> 上的跨模型迁移。</b>\n          <a href=\"https://qwen.ai/blog?id=qwen3.6-35b-a3b\" target=\"_blank\" rel=\"noopener noreferrer\">Qwen3.6-35B-A3B</a> 列为原始进化结果；<a href=\"https://arxiv.org/abs/2606.19348\" target=\"_blank\" rel=\"noopener noreferrer\">DeepSeek</a> 列为替换骨干后的迁移性能（脚手架仍在 Qwen 上进化）。准确率上标为相对初始智能体的绝对百分点提升。",
    ),
    # theory
    (
        "<h2 class=\"rv\">Reliably Better,<br><span class=\"leaf\">Across All Conditions.</span></h2>",
        "<h2 class=\"rv\">全条件下<br><span class=\"leaf\">稳定更优。</span></h2>",
    ),
    (
        "<p class=\"lede rv\">To validate MGM's design in isolation of implementation details, we instantiate controlled surrogate models for DGM, HGM, and MGM under an additive fitness landscape—where agents carry a hidden binary genotype and tasks examine subsets of scaffold-level loci.</p>",
        "<p class=\"lede rv\">为在实现细节之外验证 MGM 设计，我们在加性适应度景观下为 DGM、HGM、MGM 实例化受控代理模型——智能体携带隐藏二元基因型，任务考察脚手架级位点子集。</p>",
    ),
    (
        "<p class=\"lede rv\">We show that comparative evidence acts as diagnostic compression: by cross-referencing multiple phenotypes or genotypes, MGM narrows the candidate defect set and raises the effective fix probability of self-modification.</p>",
        "<p class=\"lede rv\">我们表明比较证据起诊断压缩作用：通过交叉引用多表型或基因型，MGM 缩小候选缺陷集并提高自我修改的有效修复概率。</p>",
    ),
    (
        "<h2 class=\"rv\">Additive <span class=\"leaf\">Fitness Landscape</span></h2>",
        "<h2 class=\"rv\">加性<span class=\"leaf\">适应度景观</span></h2>",
    ),
    ("<h3>Binary genotype · Hamming distance</h3>", "<h3>二元基因型 · 汉明距离</h3>"),
    (
        "<p>Each agent carries genotype \\(\\mathbf{g}\\in\\{0,1\\}^{L}\\); a fixed oracle \\(\\mathbf{g}^{*}=\\mathbf{1}\\) represents the optimal program. The genotype is never directly observed—utility is measured by edit distance:</p>",
        "<p>各智能体携带基因型 \\(\\mathbf{g}\\in\\{0,1\\}^{L}\\)；固定 oracle \\(\\mathbf{g}^{*}=\\mathbf{1}\\) 表示最优程序。基因型不可直接观测——效用由编辑距离度量：</p>",
    ),
    ("<h3>Task examination · success probability</h3>", "<h3>任务考察 · 成功概率</h3>"),
    (
        "<p>Each task \\(\\tau\\) examines a subset \\(R_{\\tau}\\subseteq[L]\\) with \\(|R_{\\tau}|=k\\). The agent solves \\(\\tau\\) only when all required loci are correct:</p>",
        "<p>各任务 \\(\\tau\\) 考察子集 \\(R_{\\tau}\\subseteq[L]\\)，\\(|R_{\\tau}|=k\\)。仅当所需位点均正确时智能体才解决 \\(\\tau\\)：</p>",
    ),
    (
        "<p class=\"bg-note rv\">Each run starts from \\(d_0\\) mismatched loci; at initialization, \\(P(r{=}1\\mid d_0)=((L-d_0)/L)^{k}\\)—modeling tasks that require multiple scaffold capabilities to be simultaneously correct.</p>",
        "<p class=\"bg-note rv\">每次运行从 \\(d_0\\) 个错配位点出发；初始化时 \\(P(r{=}1\\mid d_0)=((L-d_0)/L)^{k}\\)——建模需多项脚手架能力同时正确的任务。</p>",
    ),
    (
        "<h2 class=\"rv\">Comparative Evidence as <span class=\"leaf\">Diagnostic Compression</span></h2>",
        "<h2 class=\"rv\">比较证据作为<span class=\"leaf\">诊断压缩</span></h2>",
    ),
    ("<h3>Effective fix probability</h3>", "<h3>有效修复概率</h3>"),
    (
        "<p>Operator \\(\\sigma\\in\\{\\mathrm{CM},\\mathrm{RM},\\mathrm{CH}\\}\\) uses evidence \\(E\\) to form a candidate locus set \\(C_{\\sigma}(E)\\). Given per-locus repair probability \\(s\\in(0,1]\\) when targeting a truly incorrect locus:</p>",
        "<p>算子 \\(\\sigma\\in\\{\\mathrm{CM},\\mathrm{RM},\\mathrm{CH}\\}\\) 用证据 \\(E\\) 形成候选位点集 \\(C_{\\sigma}(E)\\)。命中真错配位点时每位置修复概率 \\(s\\in(0,1]\\)：</p>",
    ),
    (
        "<p>\\(\\varPhi_{\\mathrm{CM}}\\) uses \\(C_{\\mathrm{CM}}=R_{\\tau_t}\\); \\(\\varPhi_{\\mathrm{RM}}\\) compresses to \\(C_{\\mathrm{RM}}=R_{\\tau_t}\\cap R_{\\tau_r}\\); \\(\\varPhi_{\\mathrm{CH}}\\) filters \\(R_{\\tau}\\) via a contrastive reference trajectory.</p>",
        "<p>\\(\\varPhi_{\\mathrm{CM}}\\) 用 \\(C_{\\mathrm{CM}}=R_{\\tau_t}\\)；\\(\\varPhi_{\\mathrm{RM}}\\) 压缩为 \\(C_{\\mathrm{RM}}=R_{\\tau_t}\\cap R_{\\tau_r}\\)；\\(\\varPhi_{\\mathrm{CH}}\\) 经对比参考轨迹过滤 \\(R_{\\tau}\\)。</p>",
    ),
    ("<h3>Proposition 1 · Monte Carlo setup</h3>", "<h3>命题 1 · 蒙特卡洛设定</h3>"),
    (
        "<p>Under sound comparative evidence, richer operators achieve strictly higher effective fix probability:</p>",
        "<p>在有效比较证据下，更丰富的算子严格获得更高有效修复概率：</p>",
    ),
    (
        "<p class=\"bg-note rv\">Each method runs for total budget \\(B\\) with \\(n_{\\mathrm{seeds}}\\) independent seeds; \\(\\rho=1\\) gives no diagnostic advantage (MGM \\(\\approx\\) HGM), while \\(\\rho>1\\) isolates the gain from comparative evidence rather than unequal compute.</p>",
        "<p class=\"bg-note rv\">各方法在总预算 \\(B\\) 下运行 \\(n_{\\mathrm{seeds}}\\) 个独立种子；\\(\\rho=1\\) 无诊断优势（MGM \\(\\approx\\) HGM），\\(\\rho>1\\) 隔离比较证据收益而非不等计算量。</p>",
    ),
    (
        "<h2 class=\"rv\">Additive Landscape <span class=\"leaf\">Simulation.</span></h2>",
        "<h2 class=\"rv\">加性景观<span class=\"leaf\">仿真。</span></h2>",
    ),
    (
        'alt="Additive fitness landscape model illustration"',
        'alt="加性适应度景观模型示意图"',
    ),
    (
        'aria-label="Evolution dynamics: best edit distance versus cumulative cost"',
        'aria-label="进化动态：最优编辑距离随累积成本变化"',
    ),
    (
        'aria-label="Edit-distance distribution across Monte Carlo seeds"',
        'aria-label="蒙特卡洛种子间的编辑距离分布"',
    ),
    (
        "<label for=\"simD0\">Initial distance <b id=\"simD0Val\">20</b></label>",
        "<label for=\"simD0\">初始距离 <b id=\"simD0Val\">20</b></label>",
    ),
    (
        "<label for=\"simRho\">Diagnostic advantage <b id=\"simRhoVal\">2.0</b></label>",
        "<label for=\"simRho\">诊断优势 <b id=\"simRhoVal\">2.0</b></label>",
    ),
    ('aria-label="Initial distance"', 'aria-label="初始距离"'),
    ('aria-label="Diagnostic advantage"', 'aria-label="诊断优势"'),
    ("<span><i class=\"oracle\"></i>oracle</span>", "<span><i class=\"oracle\"></i>最优</span>"),
    (
        "<b>\n              Additive fitness landscape model.</b>\n            Each agent carries genotype with loci, which is only exposed through φ-evaluation, where each task τ<sub>i</sub> examines a subset of loci for a successful phenotype.\n            Φ-expansion modifies the examined loci under certain probabilities.",
        "<b>加性适应度景观模型。</b>\n            各智能体携带含位点的基因型，仅通过 φ-评估暴露；各任务 τ<sub>i</sub> 考察位点子集以形成成功表型。\n            Φ-扩展以一定概率修改所考察位点。",
    ),
    (
        "<b>\n              Real-time simulation of performance evolution over cumulative budget spent.</b>\n              The panel shows the edit distance results (lower is better) averaged across random seeds with 95% CIs.\n              Dashed lines mark oracle optima.",
        "<b>累积预算下性能演化的实时仿真。</b>\n              面板为跨随机种子平均的编辑距离（越低越好）及 95% 置信区间。\n              虚线标记最优（oracle）水平。",
    ),
    (
        "<b>Real-time simulation of final performance distribution.</b>\n              The panel shows the distribution of final edit distance results (lower is better) across all random seeds, with dashed lines marking per-method means.",
        "<b>最终性能分布的实时仿真。</b>\n              面板为所有随机种子的最终编辑距离分布（越低越好），虚线为各方法均值。",
    ),
]

missing = []
for old, new in pairs:
    if old not in text:
        missing.append(old[:100])
        continue
    text = text.replace(old, new)
if missing:
    raise SystemExit("MISSING:\n" + "\n---\n".join(missing))

# language link on cover (both masthead slides)
lang_link = (
    '<p class="lang-switch rv" style="margin-top:14px;font-family:var(--mono);font-size:clamp(10px,1.1vmin,12px);">'
    '<a href="index.html" style="color:var(--dim);text-decoration:none;border-bottom:1px solid rgba(46,139,87,.35);">English</a>'
    '</p>'
)
text = text.replace(
    '<div class="link-list rv">',
    lang_link + '\n        <div class="link-list rv">',
    1,
)

path.write_text(text, encoding="utf-8")
print("Wrote", path)
