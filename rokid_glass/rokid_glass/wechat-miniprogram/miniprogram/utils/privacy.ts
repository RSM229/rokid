/**
 * 小程序隐私授权处理。
 *
 * ⚠️ 2026-10-09 线上复盘（手机预览时选图/拍照/录像/录音全部无反应）：
 *
 * 根因有两层——
 *   1）【平台侧，主因】「微信公众平台 → 设置 → 服务内容声明 → 用户隐私保护指引」
 *      未配置。自 2023-10-17 起，不论 app.json 是否配置 __usePrivacyCheck__，
 *      隐私功能对所有小程序一律启用；未在指引中声明的接口会被平台**直接禁用**，
 *      典型报错 `chooseMedia:fail api scope is not declared in the privacy agreement`，
 *      且真机上多为静默失败（点了没反应）。
 *   2）【代码侧】此前只对「录音 / 定位」2 处调用了 wx.requirePrivacyAuthorize，
 *      而隐私接口共 8 处。微信规则：一旦主动调用 requirePrivacyAuthorize 或注册
 *      onNeedPrivacyAuthorization，即视为「开发者自管授权链路」，**官方隐私弹窗被抑制**——
 *      于是没被覆盖的接口既无官方弹窗、也无自管授权，直接失败。
 *
 * 现在的策略：**不再接管授权链路，把弹窗交还微信官方。**
 * 微信会在首次调用受管控接口时自动弹出官方隐私弹窗，覆盖 chooseMedia、
 * chooseMessageFile、相机、录音、getLocation 等**全部**隐私接口，无需在每个调用点
 * 逐一维护授权（这正是上次漏掉 6 处的原因）。
 *
 * 因此 ensurePrivacy 保留为「该操作涉及隐私接口」的语义标记，不再主动触发授权；
 * 真正的授权与弹窗由平台完成。
 *
 * ⚠️ 前提条件：必须先在微信公众平台配置 **且通过审核** 的《用户隐私保护指引》，
 *     并在其中勾选对应类型——「摄像头 / 麦克风 / 选中的照片或视频信息 /
 *     选中的文件 / 位置信息」等（只勾「相册」不足以调用 chooseMedia 的相机来源）。
 *     指引审核约需 1 小时至数小时，通过后运行态接口才可用。
 */
export function ensurePrivacy(_scope: string): Promise<boolean> {
  // 不调用 wx.requirePrivacyAuthorize / wx.onNeedPrivacyAuthorization：
  // 保持「非自管」状态，让微信官方隐私弹窗生效并覆盖全部隐私接口。
  return Promise.resolve(true)
}
