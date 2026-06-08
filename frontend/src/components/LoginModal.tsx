import { useState } from 'react';
import { Modal, Input, Form, Button, Tabs, Typography } from 'antd';

interface Props {
  open: boolean;
  onLogin: (phone: string, password: string) => Promise<void>;
  onRegister: (phone: string, password: string, displayName: string) => Promise<void>;
  onClose: () => void;
}

export default function LoginModal({ open, onLogin, onRegister, onClose }: Props) {
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('login');
  const [loginForm] = Form.useForm();
  const [registerForm] = Form.useForm();

  const handleLogin = async () => {
    const values = await loginForm.validateFields();
    setLoading(true);
    try {
      await onLogin(values.phone, values.password);
      loginForm.resetFields();
    } catch {
      // error handled by parent
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async () => {
    const values = await registerForm.validateFields();
    setLoading(true);
    try {
      await onRegister(values.phone, values.password, values.display_name);
      registerForm.resetFields();
      setActiveTab('login');
    } catch {
      // error handled by parent
    } finally {
      setLoading(false);
    }
  };

  const items = [
    {
      key: 'login',
      label: '登录',
      children: (
        <Form form={loginForm} layout="vertical" onFinish={handleLogin}>
          <Form.Item
            name="phone"
            label="手机号"
            rules={[
              { required: true, message: '请输入手机号' },
              { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的手机号' },
            ]}
          >
            <Input placeholder="请输入手机号" maxLength={11} />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password placeholder="请输入密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登录
            </Button>
          </Form.Item>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            内部测试阶段，也可使用管理员账号登录
          </Typography.Text>
        </Form>
      ),
    },
    {
      key: 'register',
      label: '注册',
      children: (
        <Form form={registerForm} layout="vertical" onFinish={handleRegister}>
          <Form.Item
            name="phone"
            label="手机号"
            rules={[
              { required: true, message: '请输入手机号' },
              { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的手机号' },
            ]}
          >
            <Input placeholder="请输入 11 位手机号" maxLength={11} />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[
              { required: true, message: '请输入密码' },
              { min: 8, message: '密码至少 8 位' },
              { pattern: /^(?=.*[A-Za-z])(?=.*\d)/, message: '密码需包含字母和数字' },
            ]}
          >
            <Input.Password placeholder="至少 8 位，需包含字母和数字" />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="姓名"
            rules={[{ required: true, message: '请输入姓名' }]}
          >
            <Input placeholder="请输入你的姓名" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              注册
            </Button>
          </Form.Item>
        </Form>
      ),
    },
  ];

  return (
    <Modal
      title="DataPilot Agent"
      open={open}
      onCancel={onClose}
      footer={null}
      width={400}
    >
      <Tabs activeKey={activeTab} onChange={setActiveTab} items={items} centered />
    </Modal>
  );
}
