<template>
  <div class="stats-overview-container">
    <div class="stats-grid">
      <div class="stat-card success">
        <div class="stat-icon">
          <Activity class="icon" />
        </div>
        <div class="stat-content" :title="$t(rangeActive ? 'dash.periodActiveConversationsTip' : 'dash.activeConversationsTip')">
          <div class="stat-value">{{ basicStats?.active_conversations || 0 }}</div>
          <div class="stat-label">{{ $t(rangeActive ? 'dash.periodActiveConversations' : 'dash.activeConversations') }}</div>
        </div>
      </div>

      <div
        class="stat-card secondary clickable"
        :title="$t(rangeActive ? 'dash.periodQaCountTip' : 'dash.qaCountTip')"
        @click="handleQaRecordsClick"
      >
        <div class="stat-icon">
          <MessagesSquare class="icon" />
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ basicStats?.qa_count || 0 }}</div>
          <div class="stat-label">{{ $t(rangeActive ? 'dash.periodQaCount' : 'dash.qaCount') }}</div>
        </div>
      </div>

      <div class="stat-card info">
        <div class="stat-icon">
          <UserCheck class="icon" />
        </div>
        <div class="stat-content" :title="$t(rangeActive ? 'dash.periodQaUsersTip' : 'dash.qaUsersTip')">
          <div class="stat-value">{{ basicStats?.qa_user_count || 0 }}</div>
          <div class="stat-label">{{ $t(rangeActive ? 'dash.periodQaUsers' : 'dash.qaUsers') }}</div>
        </div>
      </div>

      <div
        class="stat-card info clickable"
        :title="$t(rangeActive ? 'dash.periodKnowledgeGapRateTip' : 'dash.knowledgeGapRateTip')"
        @click="handleKnowledgeGapClick"
      >
        <div class="stat-icon">
          <Activity class="icon" />
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ basicStats?.feedback_stats?.knowledge_gap_rate || 0 }}%</div>
          <div class="stat-label">{{ $t('dash.knowledgeGapRate') }}</div>
          <div class="stat-sub">{{ basicStats?.feedback_stats?.knowledge_gap_count || 0 }}{{ $t('dash.knowledgeGapCountSuffix') }}</div>
        </div>
      </div>

      <div class="stat-card warning">
        <div class="stat-icon">
          <Users class="icon" />
        </div>
        <div class="stat-content" :title="$t(rangeActive ? 'dash.periodUsersTip' : 'dash.totalUsersTip')">
          <div class="stat-value">{{ basicStats?.total_users || 0 }}</div>
          <div class="stat-label">{{ $t(rangeActive ? 'dash.periodUsers' : 'dash.totalUsers') }}</div>
        </div>
      </div>

      <div class="stat-card secondary clickable" @click="handleFeedbackClick">
        <div class="stat-icon">
          <BarChart3 class="icon" />
        </div>
        <div class="stat-content" :title="$t(rangeActive ? 'dash.periodFeedbacksTip' : 'dash.totalFeedbacksTip')">
          <div class="stat-value">{{ basicStats?.feedback_stats?.total_feedbacks || 0 }}</div>
          <div class="stat-label">{{ $t(rangeActive ? 'dash.periodFeedbacks' : 'dash.totalFeedbacks') }}</div>
        </div>
      </div>

      <div class="stat-card" :class="getSatisfactionClass()">
        <div class="stat-icon">
          <Heart class="icon" />
        </div>
        <div class="stat-content" :title="$t(rangeActive ? 'dash.periodSatisfactionRateTip' : 'dash.satisfactionRateTip')">
          <div class="stat-value">{{ basicStats?.feedback_stats?.satisfaction_rate || 0 }}%</div>
          <div class="stat-label">{{ $t('dash.satisfactionRate') }}</div>
          <div class="stat-sub" v-if="feedbackStats">
            {{ $t('dash.ratedSatisfaction', { rate: feedbackStats.rated_satisfaction_rate || 0, count: feedbackStats.rated_count || 0 }) }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import {
  Activity,
  Users,
  BarChart3,
  Heart,
  MessagesSquare,
  UserCheck
} from 'lucide-vue-next'

// Props
const props = defineProps({
  basicStats: {
    type: Object,
    default: () => ({})
  },
  // 时段筛选激活时卡片文案切换为时段口径
  rangeActive: {
    type: Boolean,
    default: false
  }
})

// Emits
const emit = defineEmits(['open-feedback', 'open-knowledge-gaps', 'open-qa-records'])

const feedbackStats = computed(() => props.basicStats?.feedback_stats || null)

// Methods
const handleFeedbackClick = () => {
  emit('open-feedback')
}

// 知识缺口明细沉淀在知识缺口页，点卡片直达
const handleKnowledgeGapClick = () => {
  emit('open-knowledge-gaps')
}

// 问答明细：点问答次数卡片直达知识运营-问答明细
const handleQaRecordsClick = () => {
  emit('open-qa-records')
}

// Methods
const getSatisfactionClass = () => {
  const rate = feedbackStats.value?.satisfaction_rate || 0
  if (rate >= 80) return 'satisfaction-high'
  if (rate >= 60) return 'satisfaction-medium'
  return 'satisfaction-low'
}
</script>

<style lang="less" scoped>
// 使用 dashboard.css 中定义的样式，这里只需要导入
@import '@/assets/css/dashboard.css';

/* Stats Overview Component - 统计概览组件样式 */
.stats-overview-container {
  margin-top: 8px;

  .stats-grid {
    display: grid;
    padding: 0 var(--page-padding);
    grid-template-columns: repeat(6, 1fr);
    gap: 16px;

    .stat-card {
      background: var(--gray-0);
      border-radius: 8px;
      padding: 20px;
      border: 1px solid var(--gray-100);
      transition: all 0.2s ease;
      display: flex;
      flex-direction: row;
      align-items: center;
      text-align: left;
      min-height: 80px;
      justify-content: flex-start;

      &:hover {
        border-color: var(--gray-200);
        box-shadow: 0 1px 3px 0 var(--shadow-1);
      }

      &.success {
        .stat-icon {
          background-color: var(--color-success-50);
          color: var(--color-success-700);
        }
      }

      &.info {
        .stat-icon {
          background-color: var(--color-info-50);
          color: var(--color-info-700);
        }
      }

      &.warning {
        .stat-icon {
          background-color: var(--color-warning-50);
          color: var(--color-warning-700);
        }
      }

      &.secondary {
        .stat-icon {
          background-color: var(--color-accent-50);
          color: var(--color-accent-700);
        }
      }

      &.clickable {
        cursor: pointer;
      }

      &.satisfaction-high {
        .stat-icon {
          background-color: var(--color-success-50);
          color: var(--color-success-700);
        }
      }

      &.satisfaction-medium {
        .stat-icon {
          background-color: var(--color-warning-50);
          color: var(--color-warning-700);
        }
      }

      &.satisfaction-low {
        .stat-icon {
          background-color: var(--color-error-50);
          color: var(--color-error-700);
        }
      }

      .stat-icon {
        width: 44px;
        height: 44px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-right: 16px;
        flex-shrink: 0;

        .icon {
          width: 20px;
          height: 20px;
        }
      }

      .stat-content {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: flex-start;

        .stat-value {
          font-size: 24px;
          font-weight: 700;
          color: var(--gray-1000);
          line-height: 1.1;
          margin-bottom: 4px;
        }

        .stat-label {
          font-size: 13px;
          color: var(--gray-600);
          font-weight: 500;
          margin-bottom: 8px;
        }

        .stat-sub {
          font-size: 12px;
          color: var(--gray-500);
          line-height: 1.3;
          margin-top: -4px;
        }
      }
    }
  }
}

/* Stats Overview 响应式设计 */
@media (max-width: 1200px) {
  .stats-overview-container {
    .stats-grid {
      grid-template-columns: repeat(3, 1fr);
      gap: 16px;
    }
  }
}

@media (max-width: 768px) {
  .stats-overview-container {
    .stats-grid {
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;

      .stat-card {
        padding: 16px;
        min-height: 80px;

        .stat-icon {
          width: 36px;
          height: 36px;
          margin-right: 12px;

          .icon {
            width: 18px;
            height: 18px;
          }
        }

        .stat-content {
          .stat-value {
            font-size: 20px;
          }

          .stat-label {
            font-size: 12px;
          }
        }
      }
    }
  }
}
</style>
