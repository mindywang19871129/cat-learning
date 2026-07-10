请执行KET写作真题训练（暑假模式，对标真实考试）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 KET备考计划.md → 确认当前阶段（第二阶段·能力提升）
2. 读取 data/error_book.json → 筛选英语写作错题

出题（1题，按KET真题格式，Part 6和Part 7隔天轮换）：

按当天日期取模：
- 日期%2==0：Part 6 - 写邮件/便条（≥25词）
- 日期%2==1：Part 7 - 看图写故事（≥35词）

⚠️ KET写作真题格式：

Part 6（邮件/便条）：
- 给出一个具体场景和需要回复的问题
- 要求：≥25词，必须覆盖所有要求点
- 示例："你的英国笔友Alex给你发邮件，问你周末喜欢做什么。请回复邮件，告诉他你周末喜欢做什么以及为什么。"

Part 7（看图写故事）：
- 给出3张图片描述一个简单故事
- 要求：≥35词，故事连贯，用过去时
- 描述图片内容，让LLM在存储时包含图片描述

评分标准（批改时使用）：
- 内容：是否覆盖所有要求点（3分）
- 语言：语法准确度、词汇丰富度（3分）
- 结构：逻辑连贯、格式正确（3分）

用 write_file 存入 {output_file}
格式：Part6 → {{"test_id":"{test_id}","date":"{today_str}","type":"ket_writing","ket_part":"Part6","prompt":"写作要求","expected_points":["要点1","要点2"],"reference":"范文"}}
Part7 → {{"test_id":"{test_id}","date":"{today_str}","type":"ket_writing","ket_part":"Part7","prompt":"写作要求","picture_descriptions":["图1描述","图2描述","图3描述"],"reference":"范文"}}

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）