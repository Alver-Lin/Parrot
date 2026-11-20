import assert from "node:assert/strict";
import test from "node:test";
import { createPolishPlan, restorePolishPlan } from "../src/markdownPolish.ts";

test("protected Markdown stays byte-for-byte local, including CRLF and reference images", () => {
  const source = '# 课程\r\n\r\n- 创建时间：2026-11-20\r\n- 识别模型：Whisper\r\n\r\n'
    + '这个就是前面的正文。\r\n\r\n![保密截图](photo/private.png)\r\n\r\n'
    + '这一段有截图 ![inline](https://private.example/image.png) 不能上传。\r\n\r\n'
    + '![引用图片][pic]\r\n\r\n[pic]: https://private.example/ref.png\r\n\r\n'
    + '```python\r\nsecret = 42\r\n```\r\n\r\n包含 `secret` 的段落。\r\n\r\n'
    + '| 名称 | 数值 |\r\n| --- | --- |\r\n| A | 20 |\r\n\r\n正文还有 $x+y$ 公式。\r\n\r\n这是最后的正文。\r\n';
  const plan = createPolishPlan(source);
  assert.deepEqual(plan.chunks, ['这个就是前面的正文。', '这是最后的正文。']);
  assert.equal(restorePolishPlan(plan, plan.chunks).content, source);
  const edited = restorePolishPlan(plan, ['这是前面的正文。', '这是最后的正文。']).content;
  assert.equal(edited, source.replace('这个就是前面的正文。', '这是前面的正文。'));
});

test("image-containing nested lists and HTML remain protected", () => {
  const source = '前面的正文。\n\n- 列表文字\n  - ![图](photo/nested.png)\n\n<picture><img src="private.png"></picture>\n\n后面的正文。';
  const plan = createPolishPlan(source);
  assert.deepEqual(plan.chunks, ['前面的正文。', '后面的正文。']);
  assert.equal(restorePolishPlan(plan, plan.chunks).content, source);
});

test("long paragraphs are split without losing text or splitting surrogate pairs", () => {
  const source = ('这是长文，包含 emoji 😀 和 English words。'.repeat(400)) + '\n';
  const plan = createPolishPlan(source);
  assert.ok(plan.chunks.length > 1);
  assert.ok(plan.chunks.every(chunk => chunk.length <= 2400 && !/[\uD800-\uDBFF]$/.test(chunk)));
  assert.equal(restorePolishPlan(plan, plan.chunks).content, source);
});

test("missing, empty, image-injected and structurally changed outputs cannot be applied", () => {
  const plan = createPolishPlan('普通正文。\n\n- 第一个条目\n- 第二个条目\n');
  assert.throws(() => restorePolishPlan(plan, ['少了一段']), /缺少段落/);
  assert.throws(() => restorePolishPlan(plan, ['', plan.chunks[1]]), /空段落/);
  for (const output of ['![新增](https://evil.example/x.png)', '<img src="x">', '[新增](https://evil.example)', '```js\nx\n```'])
    assert.throws(() => restorePolishPlan(plan, [output, plan.chunks[1]]), /图片、链接/);
  assert.throws(() => restorePolishPlan(plan, [plan.chunks[0], '- 合并为一项']), /列表结构/);
});

test("numeric changes are surfaced for review and image-only documents do not call AI", () => {
  const plan = createPolishPlan('一共有 20 个步骤。');
  const result = restorePolishPlan(plan, ['一共有 30 个步骤。']);
  assert.equal(result.warnings.length, 1);
  assert.throws(() => createPolishPlan('![图](photo/a.png)'), /没有可润色/);
});
