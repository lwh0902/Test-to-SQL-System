import { useState } from 'react';
import { Modal, Input, Form, Button, Select, Typography, Alert, Steps, Divider } from 'antd';
import { DatabaseOutlined, PlusOutlined } from '@ant-design/icons';
import { createConnection, testDirectConnection, listConnections, discoverSchemaDirect } from '../services/api';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

interface Connection {
  id: string;
  name: string;
  host: string;
  port: number;
  db_name: string;
  db_user: string;
}

export default function SpaceCreateModal({ open, onClose, onCreated }: Props) {
  const [step, setStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selectedConnId, setSelectedConnId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [discoveredTables, setDiscoveredTables] = useState<string[]>([]);
  const [form] = Form.useForm();
  const [spaceForm] = Form.useForm();

  const loadConnections = async () => {
    const data = await listConnections();
    setConnections(data.connections || []);
  };

  const handleTestConnection = async () => {
    const values = await form.validateFields();
    setLoading(true);
    setTestResult(null);
    try {
      const result = await testDirectConnection({
        host: values.host,
        port: values.port || 3306,
        db_user: values.db_user,
        db_password: values.db_password,
        db_name: values.db_name,
      });
      setTestResult(result);
      if (result.ok) {
        const schemaResult = await discoverSchemaDirect({
          host: values.host,
          port: values.port || 3306,
          db_user: values.db_user,
          db_password: values.db_password,
          db_name: values.db_name,
        });
        if (schemaResult.ok && schemaResult.schema) {
          setDiscoveredTables(Object.keys(schemaResult.schema));
        }
      }
    } catch {
      setTestResult({ ok: false, error: '连接失败' });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateConnection = async () => {
    const values = await form.validateFields();
    if (!testResult?.ok) return;
    setLoading(true);
    try {
      await createConnection({
        name: values.name || `${values.host}/${values.db_name}`,
        host: values.host,
        port: values.port || 3306,
        db_user: values.db_user,
        db_password: values.db_password,
        db_name: values.db_name,
      });
      await loadConnections();
      setStep(1);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectConnection = async () => {
    if (!selectedConnId) {
      const existingConns = connections;
      if (existingConns.length === 0) return;
      setSelectedConnId(existingConns[0].id);
    }
    setStep(1);
  };

  const handleCreateSpace = async () => {
    const values = await spaceForm.validateFields();
    setLoading(true);
    try {
      const { createUserSpace } = await import('../services/api');
      await createUserSpace(values.name, selectedConnId!);
      onCreated();
      setStep(0);
      form.resetFields();
      spaceForm.resetFields();
      setTestResult(null);
      setDiscoveredTables([]);
      setSelectedConnId(null);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    setStep(0);
    setTestResult(null);
    setDiscoveredTables([]);
    setSelectedConnId(null);
    form.resetFields();
    spaceForm.resetFields();
    onClose();
  };

  return (
    <Modal
      title="创建分析空间"
      open={open}
      onCancel={handleClose}
      footer={null}
      width={520}
    >
      <Steps
        current={step}
        size="small"
        items={[{ title: '数据库连接' }, { title: '空间信息' }]}
        style={{ marginBottom: 24 }}
      />

      {step === 0 && (
        <>
          {connections.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <Typography.Text strong>已有连接</Typography.Text>
              <Select
                placeholder="选择已有连接"
                style={{ width: '100%', marginTop: 8 }}
                value={selectedConnId}
                onChange={setSelectedConnId}
                options={connections.map(c => ({
                  value: c.id,
                  label: `${c.name} (${c.host}/${c.db_name})`,
                }))}
              />
              <Button
                type="primary"
                disabled={!selectedConnId}
                onClick={handleSelectConnection}
                style={{ marginTop: 8 }}
                block
              >
                使用此连接
              </Button>
              <Divider>或新建连接</Divider>
            </div>
          )}

          <Form form={form} layout="vertical">
            <Form.Item name="name" label="连接名称">
              <Input placeholder="如：生产订单库" />
            </Form.Item>
            <div style={{ display: 'flex', gap: 8 }}>
              <Form.Item name="host" label="主机" rules={[{ required: true }]} style={{ flex: 1 }}>
                <Input placeholder="192.168.1.100" />
              </Form.Item>
              <Form.Item name="port" label="端口" initialValue={3306} style={{ width: 100 }}>
                <Input type="number" />
              </Form.Item>
            </div>
            <Form.Item name="db_name" label="数据库名" rules={[{ required: true }]}>
              <Input placeholder="my_database" />
            </Form.Item>
            <div style={{ display: 'flex', gap: 8 }}>
              <Form.Item name="db_user" label="用户名" rules={[{ required: true }]} style={{ flex: 1 }}>
                <Input placeholder="readonly_user" />
              </Form.Item>
              <Form.Item name="db_password" label="密码" rules={[{ required: true }]} style={{ flex: 1 }}>
                <Input.Password placeholder="建议只读账号" />
              </Form.Item>
            </div>

            {testResult && (
              <Alert
                type={testResult.ok ? 'success' : 'error'}
                message={testResult.ok
                  ? `连接成功${discoveredTables.length > 0 ? `，发现 ${discoveredTables.length} 张表` : ''}`
                  : `连接失败: ${testResult.error}`}
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}

            <div style={{ display: 'flex', gap: 8 }}>
              <Button onClick={handleTestConnection} loading={loading}>
                测试连接
              </Button>
              <Button
                type="primary"
                disabled={!testResult?.ok}
                onClick={handleCreateConnection}
                loading={loading}
                icon={<PlusOutlined />}
              >
                创建并继续
              </Button>
            </div>
          </Form>
        </>
      )}

      {step === 1 && (
        <Form form={spaceForm} layout="vertical" onFinish={handleCreateSpace}>
          <Form.Item name="name" label="空间名称" rules={[{ required: true, message: '请输入空间名称' }]}>
            <Input placeholder="如：订单分析" />
          </Form.Item>
          {discoveredTables.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <Typography.Text type="secondary">
                已发现 {discoveredTables.length} 张数据表：{discoveredTables.slice(0, 5).join(', ')}
                {discoveredTables.length > 5 ? '...' : ''}
              </Typography.Text>
            </div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <Button onClick={() => setStep(0)}>上一步</Button>
            <Button type="primary" htmlType="submit" loading={loading} icon={<DatabaseOutlined />}>
              创建空间
            </Button>
          </div>
        </Form>
      )}
    </Modal>
  );
}
