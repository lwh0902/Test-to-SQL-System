import { useState, type FormEvent } from 'react';
import { ShaderAnimation } from '@/components/ui/shader-animation';

interface Props {
  onLogin: (phone: string, password: string) => Promise<void>;
  onRegister: (phone: string, password: string, displayName: string) => Promise<void>;
}

type Mode = 'login' | 'register';

const PHONE_RE = /^1[3-9]\d{9}$/;
const PWD_RE = /^(?=.*[A-Za-z])(?=.*\d)/;

export default function LoginPage({ onLogin, onRegister }: Props) {
  const [mode, setMode] = useState<Mode>('login');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const switchMode = (m: Mode) => {
    setMode(m);
    setError(null);
    setPhone('');
    setPassword('');
    setDisplayName('');
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!PHONE_RE.test(phone)) {
      setError('请输入正确的 11 位手机号');
      return;
    }
    if (mode === 'register') {
      if (password.length < 8) {
        setError('密码至少 8 位');
        return;
      }
      if (!PWD_RE.test(password)) {
        setError('密码需包含字母和数字');
        return;
      }
      if (!displayName.trim()) {
        setError('请输入姓名');
        return;
      }
    } else {
      if (!password) {
        setError('请输入密码');
        return;
      }
    }

    setLoading(true);
    try {
      if (mode === 'login') {
        await onLogin(phone, password);
      } else {
        await onRegister(phone, password, displayName.trim());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '请求失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative min-h-screen w-full overflow-hidden bg-black flex items-center justify-center px-4">
      <ShaderAnimation />
      <div className="pointer-events-none absolute inset-0 bg-black/55 backdrop-blur-[6px]" />

      <div className="relative z-10 w-full max-w-md">
        <div className="text-center mb-8 select-none">
          <h1 className="text-4xl md:text-5xl font-light tracking-tight text-white drop-shadow-[0_2px_18px_rgba(0,0,0,0.6)]">
            DataPilot Agent
          </h1>
          <p className="text-white/55 text-sm mt-3 tracking-wide">AI 驱动的对话式数据分析工作台</p>
        </div>

        <div className="bg-white/[0.06] backdrop-blur-2xl border border-white/10 rounded-3xl p-8 shadow-[0_30px_80px_rgba(0,0,0,0.55)]">
          <div className="flex bg-white/5 rounded-full p-1 mb-6">
            {(['login', 'register'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                className={`flex-1 py-2 text-sm rounded-full transition-all duration-300 ${
                  mode === m
                    ? 'bg-white text-black font-medium shadow-[0_4px_18px_rgba(255,255,255,0.18)]'
                    : 'text-white/65 hover:text-white'
                }`}
              >
                {m === 'login' ? '登录' : '注册'}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <Field label="手机号">
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value.replace(/\D/g, '').slice(0, 11))}
                placeholder="请输入 11 位手机号"
                autoComplete="tel"
                className="w-full bg-transparent text-white placeholder-white/35 text-sm py-2 outline-none"
              />
            </Field>

            <Field label="密码">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === 'register' ? '至少 8 位，需含字母和数字' : '请输入密码'}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                className="w-full bg-transparent text-white placeholder-white/35 text-sm py-2 outline-none"
              />
            </Field>

            {mode === 'register' && (
              <Field label="姓名">
                <input
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="请输入你的姓名"
                  autoComplete="name"
                  className="w-full bg-transparent text-white placeholder-white/35 text-sm py-2 outline-none"
                />
              </Field>
            )}

            {error && (
              <div className="text-xs text-rose-300 bg-rose-500/10 border border-rose-400/20 rounded-lg px-3 py-2">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full mt-2 py-2.5 rounded-full bg-white text-black text-sm font-medium
                         hover:bg-white/90 active:scale-[0.99] transition-all duration-200
                         shadow-[0_10px_30px_rgba(255,255,255,0.18)]
                         disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {loading ? '处理中...' : mode === 'login' ? '登 录' : '注 册'}
            </button>
          </form>

          <p className="text-white/35 text-xs text-center mt-5">
            {mode === 'login' ? '没有账号？' : '已有账号？'}
            <button
              type="button"
              onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}
              className="ml-1 text-white/70 hover:text-white underline-offset-2 hover:underline"
            >
              {mode === 'login' ? '立即注册' : '去登录'}
            </button>
          </p>
        </div>

        <p className="text-center text-white/30 text-[11px] mt-6 tracking-wide">
          内部测试阶段 · DataPilot 可能会犯错，请核实重要数据
        </p>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-white/55 text-xs mb-1.5 tracking-wide">{label}</span>
      <div className="bg-white/5 border border-white/10 rounded-xl px-3.5 transition-colors focus-within:border-white/30 focus-within:bg-white/10">
        {children}
      </div>
    </label>
  );
}
