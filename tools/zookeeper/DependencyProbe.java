/* Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE. */
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.regex.Pattern;
import org.jline.utils.SafeRegex;
import org.jline.utils.RegexTimeoutException;
import org.apache.zookeeper.server.admin.AdminServerFactory;
import org.apache.zookeeper.server.persistence.SnapStream;
import org.xerial.snappy.Snappy;

/** Bounded controls for the server-only dependency contract, run with -Xmx64m. */
public final class DependencyProbe {
    public static void main(String[] args) throws Exception {
        System.setProperty("zookeeper.admin.enableServer", "false");
        if (!AdminServerFactory.createAdminServer().getClass().getSimpleName().equals("DummyAdminServer"))
            throw new IllegalStateException("Unexpected admin implementation");
        for (String name : new String[]{"org.eclipse.jetty.http.HttpParser", "org.eclipse.jetty.security.authentication.DigestAuthenticator"}) {
            try { Class.forName(name); throw new IllegalStateException("Jetty remains reachable"); }
            catch (ClassNotFoundException expected) { }
        }
        Class.forName("org.jline.reader.LineReaderBuilder");
        Class.forName("org.jline.utils.SafeRegex");
        if (!SafeRegex.find(Pattern.compile("synthetic"), "synthetic fixture"))
            throw new IllegalStateException("Ordinary regex failed");
        long start = System.nanoTime();
        try {
            SafeRegex.matcher(Pattern.compile("(a+)+b"), "a".repeat(4096), 5).matches();
            throw new IllegalStateException("Pathological regex was not bounded");
        } catch (RegexTimeoutException expected) {
            if (System.nanoTime() - start > 2_000_000_000L) throw new IllegalStateException("Regex budget exceeded");
        }
        byte[] original = "bounded synthetic snapshot content".getBytes(StandardCharsets.UTF_8);
        if (!Arrays.equals(original, Snappy.uncompress(Snappy.compress(original))))
            throw new IllegalStateException("Snappy round trip failed");
        // Small, invalid declared lengths exercise native guards without large
        // allocation or memory-corruption attempts against a live service.
        for (byte[] malformed : new byte[][]{new byte[]{1}, new byte[]{1,0,1}}) {
            try { Snappy.uncompressLongArray(malformed); throw new IllegalStateException("Malformed typed input accepted"); }
            catch (java.io.IOException expected) { }
        }
        File file = Files.createTempFile("ops-zk-snapshot-", ".snappy").toFile();
        try {
            SnapStream.setStreamMode(SnapStream.StreamMode.SNAPPY);
            try (java.io.OutputStream out = SnapStream.getOutputStream(file, true)) { out.write(original); }
            try (java.io.InputStream in = SnapStream.getInputStream(file)) {
                if (!Arrays.equals(original, in.readAllBytes())) throw new IllegalStateException("Snapshot read failed");
            }
        } finally { Files.deleteIfExists(file.toPath()); }
        System.out.println("SERVER_ONLY_DEPENDENCIES_PASS");
    }
}
