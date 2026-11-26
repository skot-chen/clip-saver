# ClipSaver

A small Mac / Windows app for saving videos from **X (Twitter)** and **Threads** posts.

**介绍页 / Homepage：https://skot-chen.github.io/clip-saver/**

把 X 或 Threads 的帖子链接贴进去，就能看到这条帖子里所有能下载的视频，选好以后保存到「下载」文件夹。

- **一条帖子有好几个视频时**：每个视频都列出来，带预览图、时长和清晰度，你自己勾选要哪几个。
- **视频不是这条帖子自己的时候**：会标出它来自哪里——「本帖」、「引用帖 @某某」，或者「卡片/广告」。默认只勾选本帖自己的视频。
- **默认画质最高**，也可以选低一点的。
- 下载的都是普通 mp4 文件，有画面也有声音。
- 默认直接联网，不用代理。只有连不上的时候，才需要在「设置」里填代理地址。
- 不保存任何记录：没有历史，也没有账号。电脑上只多出你下载的视频。

## 下载和安装（Mac，Apple 芯片：M1/M2/M3/M4）

1. 下载 [ClipSaver-macOS-arm64.dmg](https://github.com/skot-chen/clip-saver/releases/latest/download/ClipSaver-macOS-arm64.dmg)（或者到 [Releases 页面](../../releases/latest) 下载）。
2. 双击 dmg，把 `ClipSaver` 拖进旁边的 Applications（应用程序）文件夹。
3. **第一次打开**：双击后 Mac 会提示"无法验证开发者"（因为这个软件没有花钱找 Apple 签名）。
   - 点「完成」，然后打开 **系统设置 → 隐私与安全性**，在下面找到 ClipSaver，点 **「仍要打开」**，再输入一次开机密码。
   - 如果提示"已损坏"，打开「终端」，粘贴下面这行后回车，再双击打开：
     ```
     xattr -cr /Applications/ClipSaver.app
     ```
   以后就可以直接双击打开了。

## 下载和安装（Windows 10 / 11）

1. 下载 [ClipSaver-Windows.exe](https://github.com/skot-chen/clip-saver/releases/latest/download/ClipSaver-Windows.exe)。如果浏览器提示"不常下载"，点「…」→「保留」。
2. 不用安装，这一个文件就是整个程序。放到桌面或任何文件夹都行。
3. **第一次打开**：如果显示"Windows 已保护你的电脑"，点「更多信息」→「仍要运行」。以后就可以直接双击打开了。

## 使用

- 双击 ClipSaver，浏览器会自动打开它的页面。程序本身没有窗口，Mac 的 Dock 和 Windows 的任务栏里都不会出现图标。
- 粘贴链接 → 点「查找视频」→ 勾选 → 点「下载选中的」。
- 视频保存在「下载」文件夹，文件名像 `x_推文编号_1.mp4`、`threads_帖子编号.mp4`。
- 用完点右上角「退出」。如果直接关掉浏览器页面，程序大约 2 分钟后会自己退出。

## 下载不了怎么办

| 提示 | 原因 / 办法 |
|---|---|
| 连不上网站 | 网络连不上 X 或 Threads。打开代理软件，在「设置」里填代理地址再试一次。 |
| 没有能下载的视频 | 帖子里只有图片，或者是直播、YouTube 这类外链视频，下载不了。 |
| 看不到这条推文/帖子 | 帖子被删了、账号上了锁，或者要登录才能看。 |
| 视频地址过期了 | 页面放了太久。重新点「查找视频」再下载。 |
