Component({
  data: {
    selected: 0,
    list: [
      { pagePath: "/pages/index/index", text: "AI员工", icon: "/assets/tab/home.png", activeIcon: "/assets/tab/home_white.png" },
      { pagePath: "/pages/downloads/downloads", text: "作品", icon: "/assets/tab/works.png", activeIcon: "/assets/tab/works_white.png" },
      { pagePath: "/pages/profile/profile", text: "我的", icon: "/assets/tab/profile.png", activeIcon: "/assets/tab/profile_white.png" }
    ]
  },
  methods: {
    switchTab(e) {
      const index = Number(e.currentTarget.dataset.index || 0);
      const item = this.data.list[index];
      if (!item) return;
      wx.switchTab({ url: item.pagePath });
      this.setData({ selected: index });
    }
  }
});
