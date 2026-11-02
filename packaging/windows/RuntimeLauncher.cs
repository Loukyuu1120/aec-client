using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("AEC Studio")]
[assembly: AssemblyProduct("AEC Studio")]
[assembly: AssemblyDescription("AEC Studio launcher")]
[assembly: AssemblyVersion("0.11.7.0")]

internal static class RuntimeLauncher
{
    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    [STAThread]
    private static int Main(string[] args)
    {
        bool selfTest = args.Length > 0 && args[0] == "--self-test";
        try
        {
            string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string runtime = Path.Combine(root, "python");
            string python = Path.Combine(runtime, "AEC Studio.exe");
            if (!File.Exists(python))
                throw new FileNotFoundException("The application runtime is missing. Please reinstall AEC Studio.", python);
            string arguments = Quote(Path.Combine(root, selfTest ? "runtime_probe.py" : "launch.py"));
            int waitForPid = 0;
            if (selfTest)
            {
                for (int index = 1; index < args.Length; index++)
                {
                    if (args[index] == "--deep") arguments += " --deep";
                    else if (args[index] == "--output" && index + 1 < args.Length)
                        arguments += " --output " + Quote(Path.GetFullPath(args[++index]));
                    else throw new ArgumentException("Unknown self-test argument.");
                }
            }
            else
            {
                for (int index = 0; index < args.Length; index++)
                {
                    string argument = args[index];
                    if (argument == "--wait-for-pid" && index + 1 < args.Length)
                    {
                        if (!Int32.TryParse(args[++index], out waitForPid) || waitForPid <= 0)
                            throw new ArgumentException("Invalid restart process.");
                        continue;
                    }
                    if (argument != "--background" && argument != "--auto-start" && argument != "--no-auto-start" && argument != "--show")
                        throw new ArgumentException("Unknown application argument.");
                    arguments += " " + argument;
                }
            }
            if (waitForPid > 0)
            {
                try
                {
                    using (Process previous = Process.GetProcessById(waitForPid))
                        if (!previous.WaitForExit(30000)) throw new IOException("The previous application did not exit.");
                }
                catch (ArgumentException) { }
            }
            ProcessStartInfo start = new ProcessStartInfo(python, arguments);
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["AEC_STUDIO_INSTALLED"] = "1";
            start.EnvironmentVariables["PYTHONNET_PYDLL"] = Path.Combine(runtime, "python312.dll");
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            string tensorLibraries = Path.Combine(runtime, "Lib", "site-packages", "torch", "lib");
            if (!Directory.Exists(tensorLibraries)) tensorLibraries = Path.Combine(root, "runtime", "cpu", "torch", "lib");
            start.EnvironmentVariables["PATH"] = runtime + ";" + tensorLibraries + ";" + Environment.GetEnvironmentVariable("PATH");
            using (Process child = Process.Start(start))
            {
                if (selfTest)
                {
                    child.WaitForExit();
                    return child.ExitCode;
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            if (!selfTest && !Array.Exists(args, delegate(string value) { return value == "--background"; }))
                MessageBox.Show(error.Message, "AEC Studio", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
