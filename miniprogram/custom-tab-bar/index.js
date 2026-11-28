Component({
  data: {
    selected: 0,
    show: true,
    list: [
      { pagePath: "/pages/index/index", text: "首页", icon: "/assets/tab2/home_off.png", activeIcon: "/assets/tab2/home_on.png" },
      { pagePath: "/pages/assets/assets", text: "素材库", icon: "/assets/tab2/assets_off.png", activeIcon: "/assets/tab2/assets_on.png" },
      { pagePath: "/pages/downloads/downloads", text: "作品", icon: "/assets/tab2/works_off.png", activeIcon: "/assets/tab2/works_on.png" },
      { pagePath: "/pages/profile/profile", text: "我的", icon: "/assets/tab2/profile_off.png", activeIcon: "/assets/tab2/profile_on.png" }
    ]
  },
  methods: {
    onTap(e) {
      const index = Number(e.currentTarget.dataset.index || 0);
      const item = this.data.list[index];
      if (!item) return;
      if (item.navigate) {
        wx.navigateTo({ url: item.pagePath });
        return;
      }
      wx.switchTab({ url: item.pagePath });
      this.setData({ selected: index });
    }
  }
});
