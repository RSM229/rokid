<template>
  <div class="register-page">
    <div class="register-header">
      <div class="logo">R</div>
      <h1>注册账号</h1>
      <p>工程监理智能管理平台</p>
    </div>

    <div class="register-card">
      <van-cell-group inset>
        <div class="guide">
          <p>本平台为组织型系统，账号不对外开放自助短信注册。</p>
          <p><strong>方式一（推荐）</strong>：在微信中搜索并使用「监理通」小程序，
            微信授权即可一键创建账号，零短信、免填写。</p>
          <p><strong>方式二</strong>：由管理员在 Web 管理后台统一开通，或由项目负责人
            发放「项目邀请码」邀请你加入对应项目与岗位。</p>
        </div>
      </van-cell-group>

      <div class="register-actions">
        <van-button
          round
          block
          type="primary"
          @click="goLogin"
        >
          已有账号？去登录
        </van-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function goLogin() {
  router.replace('/login')
}
</script>

<style scoped>
.register-page {
  min-height: 100vh;
  background: #f5f7fa;
  padding: 30px 18px;
  box-sizing: border-box;
}

.register-header {
  text-align: center;
  margin-bottom: 24px;
}

.logo {
  width: 58px;
  height: 58px;
  line-height: 58px;
  margin: 0 auto 12px;
  border-radius: 16px;
  background: #1989fa;
  color: white;
  font-size: 30px;
  font-weight: bold;
}

.register-header h1 {
  margin: 0;
  font-size: 24px;
  color: #323233;
}

.register-header p {
  margin: 8px 0 0;
  color: #969799;
  font-size: 14px;
}

.register-card {
  background: white;
  border-radius: 14px;
  padding: 8px 16px 20px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.05);
}

.guide {
  padding: 12px 4px;
  font-size: 14px;
  line-height: 1.8;
  color: #646566;
}

.guide p {
  margin: 0 0 12px;
}

.register-actions {
  margin: 24px 16px 10px;
}
</style>
