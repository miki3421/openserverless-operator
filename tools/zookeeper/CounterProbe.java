/* Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE. */
import java.util.concurrent.TimeUnit;
import org.apache.curator.framework.CuratorFramework;
import org.apache.curator.framework.CuratorFrameworkFactory;
import org.apache.curator.framework.recipes.shared.SharedCount;
import org.apache.curator.retry.RetryUntilElapsed;

/** The same first-boot recipe used by OpenWhisk InstanceIdAssigner. */
public final class CounterProbe {
    public static void main(String[] args) throws Exception {
        try (CuratorFramework client = CuratorFrameworkFactory.newClient(args[0], new RetryUntilElapsed(5000, 500))) {
            client.start();
            if (!client.blockUntilConnected(10, TimeUnit.SECONDS)) throw new IllegalStateException("Not connected");
            String path = "/ops-adv-counter-probe/" + args[1];
            if (args.length > 2 && args[2].equals("read")) {
                if (java.nio.ByteBuffer.wrap(client.getData().forPath(path + "/counter")).getInt() != 1
                    || client.getData().forPath(path + "/mapping/one")[0] != 1)
                    throw new IllegalStateException("Persisted read-back failed");
                System.out.println("PERSISTED_COUNTER_PASS");
                return;
            }
            if (client.checkExists().forPath(path) != null) throw new IllegalStateException("Fresh path required");
            try (SharedCount counter = new SharedCount(client, path + "/counter", 0)) {
                counter.start();
                if (counter.getCount() != 0) throw new IllegalStateException("Wrong initial value");
                if (!counter.trySetCount(counter.getVersionedValue(), 1)) throw new IllegalStateException("CAS failed");
                client.create().creatingParentContainersIfNeeded().forPath(path + "/mapping/one", new byte[]{1});
                if (counter.getCount() != 1 || client.getData().forPath(path + "/mapping/one")[0] != 1)
                    throw new IllegalStateException("Read-back failed");
                System.out.println("FRESH_COUNTER_PASS");
            } finally {
                if (!(args.length > 2 && args[2].equals("retain")) && client.checkExists().forPath(path) != null)
                    client.delete().deletingChildrenIfNeeded().forPath(path);
            }
        }
    }
}
