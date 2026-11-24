# 家庭影视（Mobile）测试任务说明 (FamilyTV Mobile Brief)

You are testing **家庭影视** (`com.studio.osc.mobile`), a Chinese Android mobile & foldable video player app.
It does NOT require user login/registration to browse public sources, live TV, or PanSou search.

## Key flows to verify (about 30 actions)
1. **Home / Movies tab (`影视`)**:
   - Wait 5s for public config from `paseo.uniai.ccwu.cc` to load.
   - Check site chips, category chips, and poster grid. Tap a movie poster to open Detail page (`MobileDetailActivity`).
   - Check episode grid, tap play button (`立即播放` / `播放 ...`) to enter `MobilePlayActivity`.
   - Verify portrait half-screen player + bottom episode list renders cleanly; tap `全屏` to test fullscreen mode, then go back.
2. **Search + PanSou Quark Search (`搜片 / 夸克`)**:
   - Tap `搜片 / 夸克` on the Home top bar.
   - Search `"庆余年"` (or any movie name) and verify the `夸克网盘` tab lists PanSou results cleanly and tapping one opens the Quark episode list without crashing (if no Quark cookie is configured on test emulator, verify the prompt is clear and does not crash).
3. **Quark tab (`夸克`) & Privacy toggle (`显示我的夸克视频`)**:
   - Switch to `夸克` bottom tab. Confirm `TVBox_MyVideos` is **hidden by default** (shows `"默认已隐藏"` notice).
   - Switch to `设置` bottom tab. Confirm `"显示我的夸克视频（/TVBox_MyVideos）"` switch is OFF by default and `"播完 24 小时后次日自动清理（/TVBox_Cache）"` is ON by default.
   - Toggle `"显示我的夸克视频"` ON, go back to `夸克` tab, confirm UI switches away from hidden state, then toggle it back OFF.
4. **Live TV tab (`直播`)**:
   - Switch to `直播` bottom tab, tap `立即观看` to open `MobileLivePlayActivity`, check channel groups, switch fullscreen and go back without crashes.
5. **Layout & Accessibility**:
   - Check Chinese text truncation, button spacing on bottom tabs and top bar, and touch target sizes on both standard phone (`pixel_6`) and tablet/foldable (`pixel_tablet`).
