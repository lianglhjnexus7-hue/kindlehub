# Kindle 第 7 代（KT2）适配笔记

实测环境：macOS + Kindle 第 7 代（2014，600×800，167ppi，16 级灰）。
以下都是这台设备上验证过的结论，不是文档推测。

## 浏览器能力

UA：

```
Mozilla/5.0 (X11; U; Linux armv7l like Android; en-us) AppleWebKit/531.2+
(KHTML, like Gecko) Version/5.0 Safari/533.2+ Kindle/3.0+
```

WebKit 531/533 ≈ Safari 5.0。实测：

| 特性 | 支持 |
|---|---|
PNG 解码显示 | ✅ |
`<meta http-equiv="refresh">` | ✅ 可靠 |
`<img>` 自然尺寸显示 | ✅ |
`max-width:100%; height:auto` | ✅ |
`object-fit` | ❌ 需 Safari 10+ |
JavaScript | ⚠️ 默认关闭，打开后可用 |
横屏 | ❌ 浏览器锁死竖屏 |
mDNS / `.local` 主机名 | ❌ |

## 视口

实测 CSS 视口宽 **600px**，与屏幕物理宽度一致，**无缩放**。
所以图片按 600px 宽输出即可 1:1 呈现，不需要任何缩放技巧。

高度受浏览器 chrome 影响，约 730–800px。底部内容放在 760px 以上最稳。

## 关键架构选择

**服务端渲染 PNG，而不是下发 HTML/CSS。**

这条一举消掉所有前端兼容问题：JS 关闭、`object-fit` 不支持、横屏锁定、
950KB 的 SPA 打不开——都跟渲染无关，Kindle 只解码一张图。

## 刷新与残影

| 方案 | 效果 |
|---|---|
`meta refresh` 整页重载 | 可用，但边框标题反复重绘，残影明显 |
JS 局部刷 `img.src` | **推荐**，边框标题不重绘，残影大幅减轻 |
JS 监听 `window.onfocus` | 锁屏唤醒后立刻刷新，不用等周期 |

JS 可用时还会自动移除 `meta refresh`，避免两种机制叠加。
JS 再关掉会自动回退整页刷新。

采集频率与刷新周期应解耦：Mac 端 15–30 秒采一次，
屏幕 60 秒左右重绘一次。别为了让时钟每分钟跳就让整页持续重绘。

## 屏保

官方固件**无法关闭屏保**。锁屏后浏览器会挂起，唤醒后仍在原页面，
点一下屏幕或等一个刷新周期即恢复。这是不越狱的固有限制。

## 换 Wi-Fi 后失联

DHCP 会重新分配 IP，Kindle 上的旧地址随即失效。三种应对：

1. 路由器绑定静态 IP（注意 macOS 默认对每个网络用随机 MAC，
   需先在「系统设置 → Wi-Fi → 私有 Wi-Fi 地址」里关掉）
2. 手动配置静态 IP
3. 服务启动时打印当前地址，后台顶部也显示一份

`.local` 主机名不可用（浏览器不解析 mDNS）。

## 字体

macOS 中文可用：Hiragino Sans GB、STHeiti Light/Medium、Songti、Arial Unicode。
PingFang.ttc 不在 `/System/Library/Fonts/` 下。

注意 Pillow 的 `load_default()` 内置字体**无中文字形**，
必须显式指定 truetype 路径，否则中文全渲染成方框。

另外 Hiragino 的 `°C` 会有额外字距，看起来像 `° C`，
用全角 `℃`（U+2103）可避免。

## 灰度

16 级。量化时**关闭抖动**（`dither=Image.Dither.NONE`），
否则 e-ink 上会出现噪点状的脏面。