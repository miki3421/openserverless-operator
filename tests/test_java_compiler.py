# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


class JavaCompilerTests(unittest.TestCase):
    def load(self):
        spec=importlib.util.spec_from_file_location('java_compile',Path(__file__).resolve().parents[1]/'tools/runtime-security/java-compile.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_javac_warning_is_success_but_nonzero_silent_exit_is_failure(self):
        compiler=self.load()
        for code,out,err,expected in ((0,b'',b'Note: deprecated API\n',True),(0,b'compiler note\n',b'',True),(1,b'',b'',False),(1,b'',b'compile error\n',False)):
            process=Mock(returncode=code);process.communicate.return_value=(out,err)
            with self.subTest(code=code,output=bool(out),error=bool(err)),patch.object(compiler.subprocess,'Popen',return_value=process),patch('sys.stdout',new=io.StringIO()) as stdout,patch('sys.stderr',new=io.StringIO()) as stderr:
                self.assertEqual(compiler.javac(['Synthetic.java'],['synthetic.jar'],'synthetic-output'),expected)
                if expected:
                    self.assertEqual(stdout.getvalue()+stderr.getvalue(),'')
                else:
                    self.assertEqual(stdout.getvalue()+stderr.getvalue(),out.decode()+err.decode())


if __name__=='__main__':unittest.main()
