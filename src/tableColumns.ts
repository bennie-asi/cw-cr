import type { TableColumn } from './ResizableTable'

export const tableColumns = {
  mergeRequests: [
    { title: 'MR / 分支', width: 380, minWidth: 180 }, { title: '评审状态', width: 148 },
    { title: '最近更新', width: 124 }, { title: '操作', width: 216, minWidth: 176 },
  ],
  recentReviews: [
    { title: 'MR / 项目', width: 300, minWidth: 160 }, { title: '触发来源', width: 120 },
    { title: '状态', width: 130 }, { title: '评论', width: 80 }, { title: '耗时', width: 80 },
    { title: '时间', width: 160 }, { title: '', width: 56, minWidth: 48 },
  ],
  projects: [
    { title: '项目 / 仓库', width: 280, minWidth: 180 }, { title: '负责人', width: 100 },
    { title: '运行状态', width: 120 }, { title: '评审模型', width: 190 }, { title: '通知策略', width: 100 },
    { title: '最近活动', width: 130 }, { title: '', width: 60, minWidth: 48 },
  ],
  notificationHistory: [
    { title: '项目 / 事件', width: 300, minWidth: 160 }, { title: '发送时间', width: 160 },
    { title: '响应', width: 200 }, { title: '状态', width: 100 }, { title: '说明', width: 200 },
  ],
  reviewHistory: [
    { title: 'MR / 项目', width: 280, minWidth: 160 }, { title: '触发来源', width: 112 },
    { title: '生成', width: 100 }, { title: '发布', width: 100 }, { title: '通知', width: 100 },
    { title: '问题', width: 110 }, { title: '时间 / 耗时', width: 160 }, { title: '操作', width: 176, minWidth: 150 },
  ],
  projectMembers: [
    { title: '成员信息', width: 260, minWidth: 160 }, { title: '项目角色', width: 140 },
    { title: '平台角色', width: 140 }, { title: '账号状态', width: 112 }, { title: '操作', width: 152, minWidth: 120 },
  ],
  accounts: [
    { title: '用户名', width: 160, minWidth: 120 }, { title: '显示名', width: 180 },
    { title: '平台角色', width: 140 }, { title: '启用状态', width: 100 }, { title: '最近登录', width: 176 },
    { title: '操作', width: 240, minWidth: 170 },
  ],
  engineRuntime: [
    { title: '引擎', width: 260, minWidth: 160 }, { title: '能力', width: 210 },
    { title: '状态', width: 210 }, { title: '平台验证', width: 160, minWidth: 128 },
  ],
} satisfies Record<string, TableColumn[]>
