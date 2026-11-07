/* Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE. */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import org.apache.zookeeper.server.persistence.SnapStream;

/** Interchange a bounded Snappy snapshot stream between the r2/r3 classpaths. */
public final class SnapshotProbe {
    public static void main(String[] args) throws Exception {
        File file = new File(args[1]);
        byte[] value = "bounded synthetic r2 snapshot".getBytes(StandardCharsets.UTF_8);
        if (args[0].equals("write")) {
            SnapStream.setStreamMode(SnapStream.StreamMode.SNAPPY);
            try (OutputStream out = SnapStream.getOutputStream(file, true)) { out.write(value); }
        } else if (args[0].equals("read")) {
            try (InputStream in = SnapStream.getInputStream(file)) {
                if (!Arrays.equals(value, in.readAllBytes())) throw new IllegalStateException("Snapshot interchange failed");
            }
        } else throw new IllegalArgumentException("write or read required");
        System.out.println("SNAPSHOT_" + args[0].toUpperCase() + "_PASS");
    }
}
