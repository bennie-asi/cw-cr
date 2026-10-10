import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import * as Dialog from '@radix-ui/react-dialog'
import { ArrowUpRight, BookOpen, CircleHelp, Settings, ShieldCheck, X } from 'lucide-react'
import './help-menu.css'

export function HelpMenu({ platformAdmin, onConnections }: { platformAdmin: boolean; onConnections: () => void }) {
  const [open, setOpen] = useState(false)
  const [guide, setGuide] = useState<'config' | 'rules' | null>(null)
  const title = guide === 'config' ? '配置如何生效' : '评审与阻断规则'
  const showGuide = (value: 'config' | 'rules') => { setOpen(false); setGuide(value) }
  return <>
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild><button type="button" className="help-menu-trigger" aria-label="帮助"><CircleHelp size={17} /><span>帮助</span></button></Popover.Trigger>
      <Popover.Portal><Popover.Content className="help-menu-panel" align="end" sideOffset={8} collisionPadding={12} aria-label="使用帮助">
        <h2>使用帮助</h2><p className="help-menu-intro">快速了解配置与评审流程。</p>
        <button type="button" className="help-menu-item" onClick={() => showGuide('config')}><BookOpen size={18} /><span><strong>配置如何生效</strong><small>平台默认与项目配置的关系</small></span><ArrowUpRight size={16} /></button>
        <button type="button" className="help-menu-item" onClick={() => showGuide('rules')}><ShieldCheck size={18} /><span><strong>评审与阻断规则</strong><small>问题级别、解决状态及合并条件</small></span><ArrowUpRight size={16} /></button>
        {platformAdmin && <button type="button" className="help-menu-item" onClick={() => { setOpen(false); onConnections() }}><Settings size={18} /><span><strong>连接配置</strong><small>检查 GitLab、模型及通知连接</small></span><ArrowUpRight size={16} /></button>}
        <p className="help-menu-tip">接收 GitLab 回调时，请使用服务器可访问的 Webhook 地址。</p>
      </Popover.Content></Popover.Portal>
    </Popover.Root>
    <Dialog.Root open={guide !== null} onOpenChange={value => { if (!value) setGuide(null) }}>
      <Dialog.Portal><Dialog.Overlay className="help-guide-overlay" /><Dialog.Content className="help-guide-dialog" aria-describedby="help-guide-description">
        <Dialog.Title>{title}</Dialog.Title><Dialog.Description id="help-guide-description">{guide === 'config' ? '从平台要求到项目检查，了解实际生效的配置。' : '问题优先级与项目阻断门槛共同决定当前评审结论。'}</Dialog.Description>
        <Dialog.Close className="help-guide-close" aria-label="关闭帮助"><X size={18} /></Dialog.Close>
        {guide === 'config' ? <ol className="help-guide-steps"><li><strong>平台通用要求</strong><p>所有评审保留平台通用要求，包括中文输出和结构化结果。</p></li><li><strong>当前评审模式</strong><p>/review 完整评审使用评审要求；/improve 使用改进要求。</p></li><li><strong>项目专项检查</strong><p>安全性、可靠性和代码风格独立启用。项目自定义替换对应专项提示词；继承时使用平台最新要求；关闭后不参与评审。</p></li><li><strong>项目通用补充要求</strong><p>在项目策略补充仓库约定。提示词保存后对新触发的评审生效，历史模型说明保留。</p></li></ol>
          : <div className="help-guide-rules"><p><strong>P0：</strong>严重问题。所有门槛均阻断。</p><p><strong>P1：</strong>高优先级问题。标准和严格门槛阻断。</p><p><strong>P2：</strong>一般建议。严格门槛阻断。</p><p><strong>未评级：</strong>需要人工确认，始终阻断。</p><p>已解决评论不计入阻断；通知与详情使用当前项目门槛。修改检查要求或门槛后，项目自动合并开关保持原状态；后续完整评审按新要求自动检查，旧检查预览失效。</p><p>项目自动合并开启一次后持续作用于后续 MR。系统在每次合并前核验最新完整评审、总评同步、当前提交 SHA 和 GitLab 合并条件。单个 MR 的可选检查只作预览，不执行合并，也不改变项目开关。</p></div>}
        <Dialog.Close asChild><button type="button" className="button button-primary">知道了</button></Dialog.Close>
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root>
  </>
}
