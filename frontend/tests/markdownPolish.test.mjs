import assert from "node:assert/strict";
import test from "node:test";
import { createPolishPlan, restorePolishPlan } from "../src/markdownPolish.ts";

test("protected content stays local while full prose and headings are available for restructuring", () => {
  const source = '# 学习记录\r\n\r\n- 创建时间：2026-11-20\r\n- 识别模型：Whisper\r\n\r\n'
    + '这个就是前面的正文。\r\n\r\n![保密截图](photo/private.png)\r\n\r\n'
    + '```python\r\nsecret = 42\r\n```\r\n\r\n包含 `secret` 的段落。\r\n\r\n'
    + '| 名称 | 数值 |\r\n| --- | --- |\r\n| A | 20 |\r\n\r\n正文还有 $x+y$ 公式。\r\n\r\n这是最后的正文。\r\n';
  const plan = createPolishPlan(source);
  const transmitted = plan.chunks.join("");
  assert.ok(transmitted.includes('# 学习记录'));
  assert.ok(transmitted.includes('这个就是前面的正文。') && transmitted.includes('这是最后的正文。'));
  for (const secret of ['2026-11-20', 'Whisper', 'photo/private.png', 'secret', '$x+y$', '| A |'])
    assert.ok(!transmitted.includes(secret));
  assert.deepEqual(plan.images.map(({ filename, label }) => ({ filename, label })), [{ filename: 'private.png', label: '保密截图' }]);
  assert.equal(restorePolishPlan(plan, plan.chunks).content, source);
  const edited = restorePolishPlan(plan, plan.chunks.map(chunk => chunk.replace('# 学习记录', '# 正文整理').replace('这个就是前面的正文。', '这是前面的正文。')));
  for (const { text } of plan.protected) assert.ok(edited.content.includes(text));
  assert.ok(edited.content.startsWith('# 正文整理'));
});

test("a generated topic, semantic image order, captions and new sections restore without changing images", () => {
  const source = '# 学习记录\n\n## 转写原文\n\n先讲检索，再讲生成。\n\n![生成图](photo/generation.png)\n\n![检索图](photo/retrieval.png)\n';
  const plan = createPolishPlan(source);
  const [generation, retrieval] = plan.anchors;
  const output = '# RAG：检索与生成\n\n## 检索资料\n\n先检索相关资料。\n\n' + retrieval.marker
    + '\n\n*截图展示检索过程。*\n\n## 生成回答\n\n结合资料组织答案。\n\n' + generation.marker;
  const { content } = restorePolishPlan(plan, [output]);
  assert.ok(content.indexOf('photo/retrieval.png') < content.indexOf('photo/generation.png'));
  assert.ok(content.includes('![生成图](photo/generation.png)'));
  assert.ok(content.includes('![检索图](photo/retrieval.png)'));
});

test("image-containing lists, reference images, HTML, code and formulas stay intact", () => {
  const source = '# 课程\n\n前面的正文。\n\n- 列表文字\n  - ![图](photo/nested.png)\n\n'
    + '<picture><img src="private.png"></picture>\n\n![引用][pic]\n\n[pic]: https://private.example/ref.png\n\n后面的正文。';
  const plan = createPolishPlan(source);
  assert.equal(plan.images.length, 1);
  assert.equal(restorePolishPlan(plan, plan.chunks).content, source);
});

test("long documents split without losing text, splitting surrogate pairs or breaking markers", () => {
  const source = '# 长文\n\n' + ('这是长文，包含 emoji 😀 和 English words。'.repeat(400))
    + '\n\n![图](photo/a.png)\n\n' + '这是后续内容。'.repeat(1200);
  const plan = createPolishPlan(source);
  assert.ok(plan.chunks.length > 1);
  assert.ok(plan.chunks.every(chunk => chunk.length <= 8000 && !/[\uD800-\uDBFF]$/.test(chunk)));
  assert.ok(plan.anchors.every(anchor => plan.chunks.filter(chunk => chunk.includes(anchor.marker)).length === 1));
  assert.equal(restorePolishPlan(plan, plan.chunks).content, source);
});

test("omitted, duplicate and unknown transport markers do not block notes", () => {
  const plan = createPolishPlan('# 课程\n\n正文。\n\n![图](photo/a.png)\n\n```js\nx\n```');
  const marker = plan.anchors[0].marker;
  for (const output of ['# 笔记\n\n直接返回。', '# 笔记\n\n' + marker + '\n\n' + marker,
    '# 笔记\n\n' + marker.replace('_0]]', '_99]]')]) {
    const result = restorePolishPlan(plan, [output]).content;
    assert.ok(result.includes('photo/a.png'));
    assert.ok(result.includes('```js\nx\n```'));
    assert.ok(!result.includes('PARROT_FIXED_'));
  }
});

test("model output accepts normal Markdown code, tables, links, images, HTML and formulas", () => {
  const plan = createPolishPlan('# 课程\n\n普通正文。');
  for (const output of ['![新增](https://example.com/x.png)', '<img src="x">', '[资料](https://example.com)',
    '```js\nx\n```', '行内 `Embedding`。', '| 名称 | 数值 |\n| --- | --- |\n| A | 20 |', '$$x+y$$', '\\(x+y\\)'])
    assert.ok(restorePolishPlan(plan, [output]).content.includes(output), output);
});

test("notes are returned directly without result review when numeric content changes", () => {
  const plan = createPolishPlan('# 课程\n\n- 可能需要 20 个步骤。\n- 通常有两个阶段。');
  const output = '# 课程操作\n\n## 操作流程\n\n可能需要 20 个步骤，通常分为两个阶段。';
  const result = restorePolishPlan(plan, [output.replace('20', '30')]);
  assert.ok(result.content.includes('30 个步骤'));
  assert.ok(!('warnings' in result));
});

test("screenshot-only records can be organized and code-only records make no AI request", () => {
  const plan = createPolishPlan('![截图](photo/a.png)');
  assert.ok(restorePolishPlan(plan, ['# 截图笔记\n\n' + plan.anchors[0].marker]).content.includes('photo/a.png'));
  assert.throws(() => createPolishPlan('```js\nx\n```'), /没有可整理/);
});

test("missing, duplicate or deep headings do not block model output", () => {
  const plan = createPolishPlan('普通正文。');
  for (const output of ['普通正文。', '# 主题\n\n# 补充标题', '# 主题\n\n#### 深层标题'])
    assert.ok(restorePolishPlan(plan, [output]).content.includes(output));
  assert.throws(() => restorePolishPlan(plan, ['']), /空笔记/);
});

test("multiple inline screenshots keep their text together and each has an attachment", () => {
  const plan = createPolishPlan('# 演示\n\n这段演示 ![甲](photo/a.png) 和 ![乙](photo/b.png)。');
  assert.equal(plan.images.length, 2);
  assert.equal(plan.anchors.length, 1);
  const output = '# 演示笔记\n\n' + plan.anchors[0].marker;
  assert.ok(restorePolishPlan(plan, [output]).content.includes('这段演示 ![甲](photo/a.png) 和 ![乙](photo/b.png)。'));
});

test("Markdown image syntax inside code is never treated as an image attachment", () => {
  const source = '# Markdown 教程\n\n这是代码示例。\n\n```md\n![示例](photo/private.png)\n```\n\n'
    + '行内 `![示例](photo/inline.png)` 也是代码。';
  const plan = createPolishPlan(source);
  assert.equal(plan.images.length, 0);
  assert.ok(!plan.chunks.join('').includes('private.png'));
  assert.ok(!plan.chunks.join('').includes('inline.png'));
  const restored = restorePolishPlan(plan, plan.chunks);
  assert.equal(restored.content, source);
});

test("indented code preserves its indentation after surrounding text is rewritten", () => {
  const source = '# 示例\n\n这是代码。\n\n    answer = 42\n    print(answer)\n\n这里是说明。';
  const plan = createPolishPlan(source);
  const result = restorePolishPlan(plan, plan.chunks.map(chunk => chunk.replace('这是代码。', '示例代码如下。')));
  assert.ok(result.content.includes('    answer = 42\n    print(answer)'));
  assert.ok(restorePolishPlan(plan, ['# 示例\n\n代码 ' + plan.anchors[0].marker]).content.includes('    answer = 42'));
});

test("second-level headings use continuous Chinese numbers and third-level headings restart per section", () => {
  const plan = createPolishPlan('# 课程\n\n课程正文。');
  const output = '# RAG 入门\n\n## 核心概念\n\n### 嵌入模型\n\n概念说明。\n\n### （五）检索\n\n检索说明。\n\n## 8. 应用场景\n\n### 3. 智能客服\n\n应用说明。';
  const result = restorePolishPlan(plan, [output]).content;
  for (const heading of ['# RAG 入门', '## 一、核心概念', '### （一）嵌入模型', '### （二）检索', '## 二、应用场景', '### （一）智能客服'])
    assert.ok(result.includes(heading), heading);
  assert.ok(!result.includes('## 一、一、'));
});

test("numbered sections remain continuous across long-document batches", () => {
  const plan = createPolishPlan('# 长课\n\n' + '口播。'.repeat(4000));
  assert.equal(plan.chunks.length, 2);
  const result = restorePolishPlan(plan, ['# 长课\n\n## 一、概念\n\n概念说明。', '## 一、应用\n\n应用说明。']).content;
  assert.ok(result.includes('## 一、概念'));
  assert.ok(result.includes('## 二、应用'));
});

test("numbering supports more than ten sections and preserves technical values in headings", () => {
  const plan = createPolishPlan('# 技术课程\n\n正文。');
  const titles = ['GPT-4', '2026 年政策', '2.5 GB 内存', ...Array.from({length:9}, (_, index) => `主题 ${index}`)];
  const output = '# 技术课程\n\n' + titles.map(title => `## ${title}\n\n说明。`).join('\n\n');
  const result = restorePolishPlan(plan, [output]).content;
  for (const heading of ['## 一、GPT-4', '## 二、2026 年政策', '## 三、2.5 GB 内存', '## 十、主题 6', '## 十一、主题 7', '## 十二、主题 8'])
    assert.ok(result.includes(heading), heading);
});

test("protected code and image references survive heading numbering without generated warnings", () => {
  const source = '# 课程\r\n\r\n## 调用 `v1.0`\r\n\r\n正文。\r\n\r\n```md\r\n## 代码中的标题\r\n```\r\n\r\n![截图](photo/a.png)\r\n';
  const plan = createPolishPlan(source);
  const result = restorePolishPlan(plan, plan.chunks);
  assert.ok(result.content.includes('## 一、调用 `v1.0`'));
  assert.ok(result.content.includes('```md\r\n## 代码中的标题\r\n```'));
  assert.ok(result.content.includes('![截图](photo/a.png)'));
  assert.ok(!('warnings' in result));
});
