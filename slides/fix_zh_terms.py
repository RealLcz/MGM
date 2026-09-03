# -*- coding: utf-8 -*-
"""Apply standardized Chinese terminology to index.zh.html."""
from pathlib import Path

path = Path(__file__).parent / "index.zh.html"
text = path.read_text(encoding="utf-8")

# Order matters: longer / more specific first
replacements = [
    ("孟德尔哥德尔机器", "孟德尔-哥德尔机"),
    ("递归自我改进", "递归自主提升"),
    ("比较进化", "比较演化"),
    ("自我修改", "自主修改"),
    ("自我改进", "自主提升"),
    ("智能体脚手架", "智能体框架"),
    ("进化脚手架", "演化智能体框架"),
    ("初始脚手架", "初始智能体框架"),
    ("脚手架级", "智能体框架级"),
    ("脚手架仍在", "智能体框架仍在"),
    ("脚手架后的", "智能体框架后的"),
    ("进化所得档案", "演化所得档案库"),
    ("变体档案", "变体档案库"),
    ("全局失败任务池", "全局失败任务池"),
    ("维护全局失败任务池（任一已评估智能体暴露的失败）", "维护全局失败任务池（任一已评估智能体暴露的失败任务）"),
    ("形式化设定：<span class=\"leaf\">基因型与档案</span>", "形式化设定：<span class=\"leaf\">基因型与档案库</span>"),
    ("<h3>档案树 · 节点统计</h3>", "<h3>档案库树 · 节点统计</h3>"),
    ("<b>档案结构</b>", "<b>档案库结构</b>"),
    ("<b>档案</b>（存什么）", "<b>档案库</b>（存什么）"),
    ("档案已存储", "档案库已存储"),
    ("档案维护", "档案库维护"),
    ("此档案与", "此档案库与"),
    ("从档案选取", "从档案库选取"),
    ("档案中不同的", "档案库中不同的"),
    ("据档案证据", "据档案库证据"),
    ("向档案中已见", "向档案库中已见"),
    ("复用档案轨迹", "复用档案库轨迹"),
    ("档案谱系树", "档案库谱系树"),
    ("三种自我修改算子", "三种自主修改算子"),
    ("三种自主修改算子", "三种演化算子"),  # method figure - user says 演化算子
    ("编程智能体", "代码智能体"),
    ("更大骨干", "更大基座模型"),
    ("替换骨干后", "替换基座模型后"),
    ("廉价骨干上", "更低成本基座模型上"),
    ("Deepseek-V4", "DeepSeek-V4"),
    ("held-out SWE-bench", "留出的 SWE-bench"),
    ("<span><i class=\"oracle\"></i>最优</span>", "<span><i class=\"oracle\"></i>理想最优</span>"),
    ("oracle \\(\\mathbf{g}", "理想最优 \\(\\mathbf{g}"),
    ("固定 oracle", "固定理想最优"),
    ("标记最优（oracle）", "标记理想最优（oracle）"),
    ("受控代理模型", "受控替身模型"),
    ("开源模型<br>\n          <span class=\"gold leaf\">超越前沿</span>性能", "开源模型<br>\n          <span class=\"gold leaf\">超越前沿</span>性能"),
    (
        "<p class=\"lede rv\">MGM 通过反应规范突变与跨谱系杂交复用档案库轨迹，为自主修改提供更丰富指导，在无需额外任务评估与 token 开销下，显著且稳定优于 HGM 基线。</p>",
        "<p class=\"lede rv\">MGM 通过反应规范突变与跨谱系杂交复用档案库轨迹，为自主演化提供信息量充足的指导，在无需额外任务评估与 token 消耗下，显著且稳定优于 HGM 基线。</p>",
    ),
    (
        "<p class=\"lede rv\">MGM 进化智能体框架可零样本从独立编程任务（Polyglot）迁移至仓库级软件工程基准（SWE-bench Pro 与 SWE-bench Multilingual），相对基线显著提升。</p>",
        "<p class=\"lede rv\">MGM 演化得到的智能体框架可零样本从独立代码任务（Polyglot）迁移至代码仓库级软件工程基准（SWE-bench Pro 与 SWE-bench Multilingual），相对基线显著提升。</p>",
    ),
    (
        "<p class=\"lede rv\">在 Qwen3.6-35B-A3B 上进化的智能体框架切换至更大基座模型 DeepSeek-V4-Flash 或 DeepSeek-V4-Pro 时，MGM 跨模型泛化更优。</p>",
        "<p class=\"lede rv\">在 Qwen 3.6-35B-A3B 上演化的智能体框架迁移至规模更大的 DeepSeek-V4-Flash 或 DeepSeek-V4-Pro 时，MGM 展现更优的跨模型泛化能力。</p>",
    ),
    (
        "<p class=\"lede rv\">结果表明 MGM 的比较演化有助于发现真正可复用的工作流级改进，而非基准或模型特化技巧——指向可扩展的自我改进智能体开发：在小数据集与更低成本基座模型上进化的智能体框架可复用于更强模型。</p>",
        "<p class=\"lede rv\">结果表明 MGM 更丰富的比较证据信号有助于发现真正可复用的智能体框架，而非基准或模型特化技巧——指向可扩展的递归自主提升路线：在较小数据集与更低成本基座模型上演化的框架，可无缝复用于更强模型。</p>",
    ),
    (
        "<h2 class=\"rv\"><span class=\"leaf\">真正可复用</span><br>的智能体框架。</h2>",
        "<h2 class=\"rv\"><span class=\"leaf\">真正可复用</span><br>的智能体框架</h2>",
    ),
    (
        "<p class=\"bg-strip rv\"><b>迄今进展</b>主要优化<b>档案库</b>（存什么）与<b>\\(\\pi\\)-采样策略</b>（下一步评估或编辑哪个智能体）——而非编辑本身如何被条件化。</p>",
        "<p class=\"bg-strip rv\"><b>迄今进展</b>主要优化<b>智能体档案库</b>与<b>\\(\\pi\\)-采样策略</b>（评估或扩展哪个智能体）——<b>自主修改过程本身</b>仍常依赖单一智能体、单一任务上的单次失败轨迹。</p>",
    ),
    (
        "<p class=\"bg-strip rv\">档案库已存储跨任务与跨谱系轨迹——<b>MGM</b> 将其复用为 \\(\\Phi\\) 的比较证据，<b>零额外评估成本</b>。</p>",
        "<p class=\"bg-strip rv\">档案库已积累跨任务与跨谱系轨迹——<b>MGM</b> 将其复用为 \\(\\Phi\\) 的比较证据，<b>零额外任务评估与 token 消耗</b>。</p>",
    ),
    (
        "<p>仅有一个有效失败时，标准的<b>单智能体、单轨迹</b>自主修改。</p>",
        "<p>作为<b>基线算子</b>，基于<b>单一失败轨迹</b>执行传统的自主修改。</p>",
    ),
    (
        "<p><b>同基因型、多任务</b>：跨环境比较表型，暴露基因型级弱点。</p>",
        "<p><b>相同基因型、多任务</b>：对比表型，用反复出现的失败模式区分<b>基因型层面缺陷</b>与任务偶然失误。</p>",
    ),
    (
        "<p><b>不同基因型、共享任务</b>：对比轨迹以迁移可迁移的行为特征。</p>",
        "<p><b>不同谱系、共享任务</b>：对比表现，指导<b>定向能力迁移</b>并减少冗余探索。</p>",
    ),
    (
        "<p class=\"lede rv\">MGM 将扩展算子 \\(\\Phi\\) 划分为三个专用子算子——\\(\\Phi_{\\mathrm{CM}}\\)、\\(\\Phi_{\\mathrm{RM}}\\) 与 \\(\\Phi_{\\mathrm{CH}}\\)——各自由档案库中不同的诊断证据 \\(E\\) 驱动。</p>",
        "<p class=\"lede rv\">MGM 将自主修改过程解构为三种<b>演化算子</b>——\\(\\Phi_{\\mathrm{CM}}\\)、\\(\\Phi_{\\mathrm{RM}}\\) 与 \\(\\Phi_{\\mathrm{CH}}\\)——直接利用档案库维护中已积累的轨迹，<b>无额外任务评估或 token 消耗</b>。</p>",
    ),
    (
        "<h2 class=\"rv\">受控遗传的<br><span class=\"leaf\">进化。</span></h2>",
        "<h2 class=\"rv\">受控遗传的<br><span class=\"leaf\">演化</span></h2>",
    ),
    (
        "<h2 class=\"rv\">递归自主提升：<span class=\"leaf\">愿景</span></h2>",
        "<h2 class=\"rv\">递归自主提升（RSI）：<span class=\"leaf\">愿景</span></h2>",
    ),
    (
        "<h2 class=\"rv\">经验性 RSI：<span class=\"leaf\">近期进展</span></h2>",
        "<h2 class=\"rv\">经验性递归自主提升：<span class=\"leaf\">近期进展</span></h2>",
    ),
    (
        "<h1 class=\"subhead rv\"><i>基于比较演化的递归自主提升编程智能体</i></h1>",
        "<h1 class=\"subhead rv\"><i>基于比较演化构建递归自主提升的代码智能体</i></h1>",
    ),
    (
        'content="孟德尔-哥德尔机（MGM）：通过孟德尔式比较进化实现递归自主提升的编程智能体——克隆突变、反应规范突变与跨谱系杂交。"',
        'content="孟德尔-哥德尔机（MGM）：通过比较演化构建递归自主提升的代码智能体——克隆突变、反应规范突变与跨谱系杂交。"',
    ),
    (
        'content="基于比较演化的递归自主提升编程智能体。35B 开源模型在 Polyglot 上 50.8% → 93.3%，超越 GPT-5。"',
        'content="基于比较演化的递归自主提升代码智能体。350 亿参数开源 Qwen 3.6 在 Polyglot 上 50.8% → 93.3%，以不足 GPT-5 百分之一的参数量实现超越。"',
    ),
    (
        "<title>孟德尔-哥德尔机 · Mendel Gödel Machine</title>",
        "<title>孟德尔-哥德尔机（MGM）</title>",
    ),
    (
        "<p class=\"lede rv\">我们表明比较证据起诊断压缩作用：通过交叉引用多表型或基因型，MGM 缩小候选缺陷集并提高自主修改的有效修复概率。</p>",
        "<p class=\"lede rv\">我们从理论上证明：MGM 对比较证据的利用可视为一种<b>信息压缩</b>——交叉引用多组基因型或表型，可靠分离反复出现的缺陷、过滤非因果位点，显著提高自主修改的有效进化概率。</p>",
    ),
    (
        "<p class=\"lede rv\">为在实现细节之外验证 MGM 设计，我们在加性适应度景观下为 DGM、HGM、MGM 实例化受控替身模型——智能体携带隐藏二元基因型，任务考察智能体框架级位点子集。</p>",
        "<p class=\"lede rv\">为验证 MGM 设计，我们构建<b>加性适应度景观</b>替身模型——基因型代表智能体框架层面的能力，各任务检验特定能力子集，全部正确才呈现成功表型。</p>",
    ),
    (
        "<p class=\"bg-note rv\">每次运行从 \\(d_0\\) 个错配位点出发；初始化时 \\(P(r{=}1\\mid d_0)=((L-d_0)/L)^{k}\\)——建模需多项智能体框架能力同时正确的任务。</p>",
        "<p class=\"bg-note rv\">每次运行从 \\(d_0\\) 个错配位点出发；\\(P(r{=}1\\mid d_0)=((L-d_0)/L)^{k}\\)——对应需多项框架能力同时正确的编码任务。</p>",
    ),
    (
        "<h2 class=\"rv\">比较证据作为<span class=\"leaf\">诊断压缩</span></h2>",
        "<h2 class=\"rv\">比较证据的<span class=\"leaf\">信息压缩</span></h2>",
    ),
    (
        "<h3>Proposition 1 · Monte Carlo setup</h3>",
        "<h3>命题 1 · 蒙特卡洛设定</h3>",
    ),
    (
        "<p>在有效比较证据下，更丰富的算子严格获得更高有效修复概率：</p>",
        "<p>在有效比较证据下，\\(\\varPhi_{\\mathrm{RM}}\\) 与 \\(\\varPhi_{\\mathrm{CH}}\\) 严格优于 \\(\\varPhi_{\\mathrm{CM}}\\)：</p>",
    ),
    (
        "<li>每次编辑仅条件于<b>一个智能体 × 一条轨迹 × 一个任务</b>（通常是近期失败）。</li>",
        "<li>每次编辑仅依赖<b>单一智能体 × 单一任务 × 单次失败轨迹</b>。</li>",
    ),
    (
        "<li>档案库仅作<b>采样排行榜</b>，未作为更丰富编辑的比较证据。</li>",
        "<li>档案库仅用于<b>采样排序</b>，未充分用作自主修改的<b>比较证据</b>。</li>",
    ),
    (
        "<p class=\"bg-strip rv\">遵循孟德尔受控比较原则，MGM 将这些信号映射为三种 \\(\\Phi\\) 算子（CM / RM / CH）。</p>",
        "<p class=\"bg-strip rv\">受孟德尔遗传学「对照比较分离可遗传效应」启发，MGM 将这些信号映射为三种演化算子（CM / RM / CH）。</p>",
    ),
    (
        "<p class=\"bg-note rv\"><b>MGM 继承此档案库与 \\(\\pi\\)-采样骨架</b>——差异在于 \\(\\Phi\\) 如何从比较轨迹构造证据 \\(E\\)。</p>",
        "<p class=\"bg-note rv\"><b>MGM 继承 HGM 的档案库与 \\(\\pi\\)-采样骨架</b>——差异在于 \\(\\Phi\\) 如何利用档案库中的<b>比较轨迹</b>构造证据 \\(E\\)。</p>",
    ),
    (
        "<b>Polyglot 上编程智能体性能与模型规模。</b>",
        "<b>Polyglot 上代码智能体性能与模型规模。</b>",
    ),
    (
        "<b>在 <a href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Verified</a> 与 <a href=\"https://aider.chat/2024/12/21/polyglot.html\" target=\"_blank\" rel=\"noopener noreferrer\">Polyglot</a> 上进化的编程智能体性能。</b>",
        "<b>在 <a href=\"https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf\" target=\"_blank\" rel=\"noopener noreferrer\">SWE-bench Verified</a> 与 <a href=\"https://aider.chat/2024/12/21/polyglot.html\" target=\"_blank\" rel=\"noopener noreferrer\">Polyglot</a> 上演化的代码智能体性能。</b>",
    ),
    (
        "<p class=\"hs-tag\">跨基准<br><i>Polyglot → SWE-bench Pro &amp; Multilingual</i></p>",
        "<p class=\"hs-tag\">跨基准迁移<br><i>Polyglot → SWE-bench Pro &amp; Multilingual</i></p>",
    ),
    (
        "<h2 class=\"rv\"><span class=\"leaf\">跨基准</span><br>与跨模型迁移。</h2>",
        "<h2 class=\"rv\"><span class=\"leaf\">跨基准</span><br>与跨模型泛化</h2>",
    ),
    (
        "<h2 class=\"rv\">全条件下<br><span class=\"leaf\">稳定更优。</span></h2>",
        "<h2 class=\"rv\">全条件下<br><span class=\"leaf\">稳定更优</span></h2>",
    ),
    (
        "<h2 class=\"rv\">加性景观<span class=\"leaf\">仿真。</span></h2>",
        "<h2 class=\"rv\">加性适应度景观<span class=\"leaf\">仿真</span></h2>",
    ),
    (
        "<b>孟德尔-哥德尔机。</b> MGM 基于跨任务与跨谱系证据，通过受控遗传组织自主修改。\n            档案库维护智能体变体谱系树；各节点以源码为基因型、评估结果为表型。\n            每轮迭代以 π-采样从档案库选取操作与资源。\n            φ-评估在未测任务上运行智能体并记录轨迹与结果。\n            克隆突变 Φ<sub>CM</sub> 依据目标任务 τ<sub>t</sub> 的单条失败轨迹编辑。\n            反应规范突变 Φ<sub>RM</sub> 利用参考任务 τ<sub>r</sub> 上的多条轨迹。\n            跨谱系杂交 Φ<sub>CH</sub> 引用尝试同一任务的其他谱系参考智能体 a<sub>r</sub>。",
        "<b>孟德尔-哥德尔机（MGM）。</b> 基于跨任务与跨谱系比较证据，通过受控遗传组织自主修改。\n            档案库维护智能体变体谱系树；节点存源码（基因型）与评估结果（表型）。\n            每轮以 π-采样决定评估（φ）或扩展（Φ）。\n            φ-评估在未测任务上运行智能体并记录轨迹。\n            克隆突变 Φ<sub>CM</sub> 基于目标任务 τ<sub>t</sub> 的单条失败轨迹。\n            反应规范突变 Φ<sub>RM</sub> 对比同一基因型在 τ<sub>t</sub> 与 τ<sub>r</sub> 上的表型。\n            跨谱系杂交 Φ<sub>CH</sub> 对比不同谱系智能体 a<sub>t</sub>、a<sub>r</sub> 在共同任务上的轨迹。",
    ),
]

missing = []
for old, new in replacements:
    if old not in text:
        missing.append(old[:90])
        continue
    text = text.replace(old, new)

# Fix accidental double replacement if any
text = text.replace("三种演化算子", "三种演化算子", 1)  # noop guard

if missing:
    print("WARN missing:", len(missing))
    for m in missing:
        print(" -", m)

path.write_text(text, encoding="utf-8")
print("Updated", path)
