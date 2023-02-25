import os

class Term:
    """
    Pretty colors for output.
    """
    PURPLE = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    def style(string, bold=False, underline=False):
        if bold is True:
            string = Term.BOLD + string;
            
        if underline is True:
            string = Term.BOLD + string;
        
        string = string + Term.ENDC
        
        return string


    def colorize(string, color):
        string = color + string + Term.ENDC
        return string
        

    def warn(string, bold=False, underline=False):
        string = Term.style(string, bold, underline)
        string = Term.colorize(string, Term.YELLOW)
        print(string)
        
        
    def ok(string, bold=False, underline=False):
        string = Term.style(string, bold, underline)
        string = Term.colorize(string, Term.GREEN)
        print(string)


    def info(string, bold=False, underline=False):
        string = Term.style(string, bold, underline)
        print(string)

        
    def fail(string, bold=False, underline=False):
        string = Term.style(string, bold, underline)
        string = Term.colorize(string, Term.RED)
        print(string)


class Directory:
    def is_cwd_in_user_dir():
        cwd = os.getcwd()
        user_dir = os.path.expanduser("~")
    
        if cwd.startswith(user_dir):
            return True
        else:
            Term.fail("Current working dir must be inside user dir. Working dir: %s, user dir: %s" % (cwd, user_dir))
            return False
