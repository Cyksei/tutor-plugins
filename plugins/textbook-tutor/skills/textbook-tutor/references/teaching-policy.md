# 流程与证据检查

先用 read_course 读取当前状态与哈希，在正式教学回复之前调用 plan_teaching_turn(note, expected_sha256, event)。它只读、不写入或发消息。按返回 action 组织本轮回复，再用同一 event 调用 record_event 保存实际发生的讲解/提示和新状态；保存工具在锁内重算规则，不依赖模型照抄决策结果。预检之后文件变更则重新读取、重评，不覆盖。不要在保存失败时声称已保存。

## 评价输入

先依据教材确定本题考查能力、合理答案范围与必要条件，再对照学生实际回答。不把完整标准答案写入未答题的可见笔记或 HTML 注释；注释不保密。工具不自动验证这些语义判断，学生质疑时先核对题目和教材。

实际作答事件 outcome 为 independent_correct / hinted_correct / incorrect，必须附 assessment：

- concept_id：稳定的章/节/概念标识；同一概念延用，新概念新 ID。
- question：与已保存 pending_question 完全相同，用于拒绝旧题作答。
- answer：实际答案的原话或忠实摘要；不能把老师的推理写成学生的回答。
- evidence：本次实际展示或缺少的判断依据。不要存未讲解的正确答案。
- support：independent / hinted / explained，表示这次回答的支持程度。
- demonstration：recognition / reasoning / application / transfer / recall。单纯选项命中用 recognition；不能因为选对就编造推理。

首次答错用 incorrect，工具返回 hint_then_wait；state 保持 position、pending_question、question_options，hint_stage=hint_given，并在 hints_given 末尾追加实际提示。提示后仍错仍用 incorrect，返回 explain，讲清后保存同一 position、hint_stage=explained_awaiting_check；可设置一个新的短变式 pending_question，清理旧题提示与选项。变式独立完成可以用 independent_correct；原题看过解释后的重复回答用 hinted_correct/support=explained。解释本身不能记成已掌握。

结果正确且足以继续时按支持程度记录 correct，返回 advance；本轮进入下一小块并保存新待答题，hint_stage=awaiting_answer、hints_given=[]。最终收尾或没有新题时 pending_question=null、hint_stage=none。正确但关键依据不明时先用 unanswered 做一次定向澄清，保留原题和阶段；不要把没有依据硬记成错答。

没有实际回答、自述“懂了”、答疑、暂停或仅切速度均不提交 assessment。正常等待用 unanswered，改速度用 preference。备课和首次讲解用 taught；已有待答题不能用 taught 偷跳，备课材料可以按原规范合并到笔记。

## 学生明确控制

保留学生主动权。event.student_request 和 request_evidence 记录其实际请求（不是模型自己编的理由），不附 assessment：

| student_request | outcome | 动作 |
|---|---|---|
| skip | skipped | 跳过并推进，不记掌握 |
| direct_explain | explained | 直接在当前要点讲解，保存 explained_awaiting_check |
| pause | unanswered | 保留题目和提示阶段 |
| preference | preference | 改偏好，保留待答状态 |
| question_repair | unanswered | 修正有歧义的题，保持当前知识位置，新题 awaiting_answer |

提示后学生明确说仍不会，可以 outcome=explained，不虚构第二次错误。首次说不会时先提示；明确要求直接讲才用 direct_explain。

## 工具生成的记录与节奏

learning.concepts 与学习事件在独立的课程进度 Markdown 原子保存；知识笔记另用 update_knowledge 整理，read_course/resume_course 自动返回。它由工具生成，不放进 state 或手工伪造；旧课第一次更新时从新证据渐进建立，不根据旧的“已讲”补造掌握。

各概念保存最近 12 次实际评价、最近状态、连续独立实质作答次数及复习触发条件。历史完整事件仍保留在原文件。recognition 不算加速依据；提示后改正不算独立作答。当前默认同一概念连续 3 次有推理/应用等证据才建议快讲，错误或支持依赖建议局部拆小。不要为了凑次数增加练习。跨概念不继承加速结论。

pace_advice 只是针对该 concept_id 的建议；尊重学生速度上下限与局部选择。pace 建议使用 {"default":"standard", "auto_adjust":true, "local":null}，固定速度用 auto_adjust=false；旧的字符串/其他偏好仍可读，不自动改写。模型决定具体例子、图示和支撑量。复习触发条件供下次相关应用/开课选题，不声称后台定时复习或精确记忆概率。

checkpoint/resume 保留真实进度、题目、提示、内容与速度，不借保存推进。旧课第一次建立状态可按原笔记真实内容保存 checkpoint。工具拒绝不一致数据时修正本轮计划/记录，不伪造学生请求绕过；如果已输出不合规则的回复，如实修正课堂状态，不把事实改成理想流程。

工具没有加载时沿用技能规则与文件读写，明确本轮没有程序检查，不声称强制保障。预检不能撤回已发出的回复，也不能保证模型一定调用工具；因此必须先预检再教学。本功能保持普通对话交互，不自动打开面板。
