const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { test } = require("node:test");

const css = readFileSync(resolve(__dirname, "../style.css"), "utf8");
const palette = Object.fromEntries([...css.matchAll(/--([a-z-]+): (#[0-9a-f]{6})/g)].map(match => [match[1], match[2]]));
function luminance(hex) {
  const channels = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
function contrast(left, right) {
  const values = [luminance(left), luminance(right)].sort((left, right) => right - left);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test("绿色主题主辅文字与状态文字至少4.5:1对比度", () => {
  for (const foreground of [palette.ink, palette.muted, palette.accent, palette.running, palette.warning, "#a4443e"]) {
    for (const background of [palette.paper, palette.rail, palette.surface, palette.selected]) {
      assert.ok(contrast(foreground, background) >= 4.5, `${foreground} on ${background}`);
    }
  }
  assert.ok(contrast(palette.surface, palette.accent) >= 4.5);
  assert.notEqual(palette.paper, "#ffffff");
});
