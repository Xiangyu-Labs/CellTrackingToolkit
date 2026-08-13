import { state } from "./state.js?v=3";

const copy = {
  zh: {
    skip:"跳到主要内容", workflow:"分析流程", segmentation:"目标分割", tracking:"细胞追踪", compare:"分组比较", search:"搜索数据集",
    selected:"个已选择", selectVisible:"全选当前结果", clearVisible:"清空当前结果", dataset:"数据集", images:"图片", status:"处理状态", empty:"没有匹配的数据集",
    all:"全部", idleSeg:"未分割", idleTrack:"未追踪", queued:"等待中", processingSeg:"分割中", processingTrack:"处理中", completedSeg:"已分割", completedTrack:"已追踪", failed:"失败", cancelling:"正在取消",
    choose:"请选择数据集", runSeg:"开始分割", runTrack:"开始追踪", runCount:"将处理 {count} 个数据集", cancelBatch:"取消整批", confirmCancel:"确定取消整个批次吗？已完成的数据会保留。",
    addGroup:"添加分组", groupName:"分组名称", groupSearch:"搜索可追踪数据集", removeGroup:"删除分组", parameters:"比较参数", fallbackTracks:"没有长轨迹时使用全部轨迹",
    figures:"比较图", temporalMetrics:"时序指标", summaryMetrics:"汇总指标", generate:"生成比较", comparisonFailed:"比较任务失败", ready:"{count} 个分组已就绪", incomplete:"每个分组至少选择一个数据集且名称不能重复。",
    history:"历史分析", noHistory:"还没有分析结果", deleteResult:"删除分析结果", confirmDelete:"永久删除这次分析及本地生成文件？", generated:"生成于", tracks:"条轨迹",
    loading:"正在生成预览", segResult:"分割结果", trackResult:"追踪结果", requestFailed:"请求失败", groupLimit:"最多支持 6 个分组", deleteFailed:"删除失败"
  },
  en: {
    skip:"Skip to content", workflow:"Workflow", segmentation:"Segmentation", tracking:"Tracking", compare:"Compare", search:"Search datasets",
    selected:"selected", selectVisible:"Select visible", clearVisible:"Clear visible", dataset:"Dataset", images:"Images", status:"Status", empty:"No matching datasets",
    all:"All", idleSeg:"Not segmented", idleTrack:"Not tracked", queued:"Waiting", processingSeg:"Segmenting", processingTrack:"Processing", completedSeg:"Segmented", completedTrack:"Tracked", failed:"Failed", cancelling:"Cancelling",
    choose:"Select datasets", runSeg:"Run segmentation", runTrack:"Run tracking", runCount:"Process {count} datasets", cancelBatch:"Cancel batch", confirmCancel:"Cancel the entire batch? Completed data will be kept.",
    addGroup:"Add group", groupName:"Group name", groupSearch:"Search tracked datasets", removeGroup:"Remove group", parameters:"Compare parameters", fallbackTracks:"Use all tracks when no long tracks qualify",
    figures:"Figures", temporalMetrics:"Temporal metrics", summaryMetrics:"Summary metrics", generate:"Generate comparison", comparisonFailed:"Comparison task failed", ready:"{count} groups ready", incomplete:"Each group needs a unique name and at least one dataset.",
    history:"Analysis history", noHistory:"No analysis results yet", deleteResult:"Delete analysis", confirmDelete:"Permanently delete this analysis and its local generated files?", generated:"Generated", tracks:"tracks",
    loading:"Generating preview", segResult:"Segmentation result", trackResult:"Tracking result", requestFailed:"Request failed", groupLimit:"A maximum of 6 groups is supported", deleteFailed:"Delete failed"
  }
};

export function t(key, values = {}) {
  let value = copy[state.language][key] || copy.zh[key] || key;
  for (const [name, replacement] of Object.entries(values)) value = value.replaceAll(`{${name}}`, replacement);
  return value;
}

export function applyStaticTranslations(root = document) {
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  root.querySelectorAll("[data-i18n]").forEach(element => { element.textContent = t(element.dataset.i18n); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach(element => { element.placeholder = t(element.dataset.i18nPlaceholder); });
  root.querySelectorAll("[data-i18n-aria]").forEach(element => { element.setAttribute("aria-label", t(element.dataset.i18nAria)); });
}
