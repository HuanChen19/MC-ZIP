# Ore UI 参考与实现依据

本次界面重做前先核对官方截图与公开代码. 查阅日期: 2026-10-02.

## 官方资料

- [Mojang: Creating Create New World][screen]:
  Bedrock UI 团队介绍新版创建世界的布局和交互.
  页面中的 `cnw-done.jpg` 是本项目布局的主要视觉参考.
- [Mojang Ore UI 仓库][ore]:
  基于 React/TypeScript 的游戏界面基础设施.
  公开组件主要为 React Facet, 不是完整的 Minecraft 视觉组件库.
- [React Facet 官方文档][facet]:
  高性能 React 状态与 DOM 更新基础.

[screen]: https://www.minecraft.net/en-us/article/creating-create-new-world
[ore]: https://github.com/Mojang/ore-ui
[facet]: https://react-facet.mojang.com/

## 样式代码参考

[参考实现 Spectrollay-OreUI][reference]提供了语义化配色、
按钮高光、按下状态和字体分工. 它是第三方实现.

其 `src/components/design/colors/style.css` 以原样附带版权声明的方式
保存为 `frontend/css/vendor/ore-colors.css`,
MIT 授权保存为同目录的 `OreUI-LICENSE.txt`.
按钮边框与高光参考其 `src/components/controls/button/style.css`.

[reference]: https://github.com/Spectrollay-OreUI/OreUI

## 在 MC-ZIP 中的对应

| 官方截图特征 | 实现 |
| --- | --- |
| 顶栏浅灰、居中标题、返回按钮 | `.ore-header`, `.header-home` |
| 左侧预览和主操作 | `.project-preview`, `.sidebar-actions` |
| 左侧分类列表、右侧设置 | `.sidebar-nav`, `.app-panel` |
| 深灰内容和分隔设置区域 | `.settings-section`, `.setting-row` |
| 浅灰次要按钮、绿色主操作 | `.ore-btn`, `.ore-btn.primary` |
| 直角边框和按钮底部阴影 | 2px 描边、4px 内阴影、按下偏移 |
| 明确的开关状态 | 凹陷轨道、立体滑块和 I/O 状态标记 |
| 白色焦点框 | `:focus-visible` |

主要颜色: 中性背景 `#48494A`, 深色区域 `#313233`,
边框 `#1E1E1F`, 次要按钮 `#D0D1D4`,
主操作 `#3C8527`, 主操作阴影 `#1D4D13`.

中文界面使用系统中文字体, 拉丁正文使用本地 Noto Sans.
Noto Sans 来源于 Fontsource 分发, 随附 SIL OFL 许可.
Silkscreen 仅用于项目预览中的小型英文标识, 不是 Mojang 字体.
没有将 Minecraft 专有字体称为已获得的官方资源.

## 实现边界

MC-ZIP 的控件用 HTML/CSS/JavaScript 实现, 支持离线使用.
没有引入 Mojang 的内部 Coherent 游戏运行环境,
也没有宣称拥有完整官方组件源码.

亮色主题以官方截图的浅灰顶栏和深灰内容区为基准.
暗色主题及窄屏布局是对同一设计语言的应用适配.
原来的 Java 资源包九宫格贴图不再参与界面控件渲染.
