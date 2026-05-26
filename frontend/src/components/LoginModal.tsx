import { useState } from 'react';
import { Modal, Input, Form, Button } from 'antd';

interface Props {
  open: boolean;
  onLogin: (username: string, password: string) => Promise<void>;
  onClose: () => void;
}

export default function LoginModal({ open, onLogin, onClose }: Props) {
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const handleOk = async () => {
    const values = await form.validateFields();
    setLoading(true);
    try {
      await onLogin(values.username, values.password);
      form.resetFields();
    } catch {
      // error handled by parent
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="登录 DataPilot"
      open={open}
      onCancel={onClose}
      footer={null}
      width={400}
    >
      <Form form={form} layout="vertical" onFinish={handleOk}>
        <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
          <Input placeholder="admin" />
        </Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true }]}>
          <Input.Password placeholder="datapilot123" />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} block>
            登录
          </Button>
        </Form.Item>
        <div style={{ color: '#999', fontSize: 12 }}>
          默认账号: admin / datapilot123
        </div>
      </Form>
    </Modal>
  );
}
