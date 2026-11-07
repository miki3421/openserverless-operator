/* Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE. */
import java.util.*;
import java.util.jar.*;

/** Fail the build on mixed HTTP versions or changes to Tika's non-server code. */
public final class VerifyHttpDependencies {
    public static void main(String[] args) throws Exception {
        try (JarFile original = new JarFile(args[0]); JarFile candidate = new JarFile(args[1])) {
            int cxf = 0, jetty = 0, tika = 0;
            for (JarEntry e : Collections.list(candidate.entries())) {
                String name = e.getName();
                if (name.startsWith("META-INF/maven/") && name.endsWith("/pom.properties")) {
                    Properties p = new Properties();
                    try (var in = candidate.getInputStream(e)) { p.load(in); }
                    String group = p.getProperty("groupId", ""), version = p.getProperty("version", "");
                    if (group.equals("org.apache.cxf")) {
                        if (!version.equals("4.1.9")) throw new IllegalStateException("Mixed CXF versions");
                        cxf++;
                    }
                    if (group.startsWith("org.eclipse.jetty")) {
                        if (!version.equals("12.0.39")) throw new IllegalStateException("Mixed Jetty versions");
                        jetty++;
                    }
                    if (group.equals("org.apache.tika") && !version.equals("3.3.3"))
                        throw new IllegalStateException("Changed Tika version");
                }
            }
            if (cxf != 7 || jetty != 7) throw new IllegalStateException("Unexpected HTTP module closure");
            for (JarEntry e : Collections.list(original.entries())) {
                String name = e.getName();
                if (name.startsWith("org/apache/tika/") && !name.startsWith("org/apache/tika/server/") && name.endsWith(".class")) {
                    JarEntry other = candidate.getJarEntry(name);
                    if (other == null) throw new IllegalStateException("Missing Tika class: " + name);
                    try (var a = original.getInputStream(e); var b = candidate.getInputStream(other)) {
                        if (!Arrays.equals(a.readAllBytes(), b.readAllBytes()))
                            throw new IllegalStateException("Changed non-server Tika class: " + name);
                    }
                    tika++;
                }
            }
            if (tika != 1293) throw new IllegalStateException("Unexpected non-server Tika inventory");
            for (String legacy : new String[]{"org/eclipse/jetty/servlet/ServletContextHandler.class", "org/eclipse/jetty/server/handler/HandlerWrapper.class"})
                if (candidate.getJarEntry(legacy) != null) throw new IllegalStateException("Legacy Jetty class remains: " + legacy);
            System.out.println("HTTP_CLOSURE_PASS cxf=" + cxf + " jetty=" + jetty + " unchangedTikaClasses=" + tika);
        }
    }
}
