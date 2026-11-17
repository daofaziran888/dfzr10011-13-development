import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    shiftLabel: '白班 08:00-20:00',
    scope: '分布式采集调度引擎',
    // 当前值班团队：断点续采的恢复权限与只读范围都按它判定。
    team: '采集一组',
    teams: ['采集一组', '采集二组', '采集三组'],
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setTeam(team: string) {
      this.team = team
    },
  },
})
